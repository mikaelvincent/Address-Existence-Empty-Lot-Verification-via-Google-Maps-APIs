"""Geocoding integration.

- Reads data/normalized.csv
- Calls Google Geocoding API with retries/backoff and concurrency
- Parses: geocode_status, lat, lng, location_type
- Caching: stores ONLY lat/lng with TTL ≤ 30 days (policy‑compliant)
- Writes:
    * data/geocode.csv
    * data/logs/geocode_api_log.jsonl (API attempt logs, PII‑safe)
- Deterministic: preserves input order; no timestamps in CSV output

Compliance:
- Do NOT cache other Google Maps response content beyond lat/lng and permitted IDs.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import config_loader  # type: ignore


# ------------------------------
# Data models
# ------------------------------


@dataclass(frozen=True)
class GeocodeResult:
    input_id: str
    input_address_raw: str
    geocode_status: str
    lat: Optional[float]
    lng: Optional[float]
    location_type: str
    api_error_codes: List[str]


# ------------------------------
# Cache (SQLite) — lat/lng only
# ------------------------------


def _ensure_cache_db(db_path: str) -> None:
    Path(os.path.dirname(db_path) or ".").mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_cache (
                input_id TEXT PRIMARY KEY,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                cached_at_utc TEXT NOT NULL
            )
            """
        )
        conn.commit()


def cache_set_latlng(db_path: str, input_id: str, lat: float, lng: float) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO geocode_cache (input_id, lat, lng, cached_at_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(input_id) DO UPDATE SET
                lat=excluded.lat,
                lng=excluded.lng,
                cached_at_utc=excluded.cached_at_utc
            """,
            (input_id, lat, lng, now),
        )
        conn.commit()


def cache_get_latlng(
    db_path: str, input_id: str, ttl_days: int
) -> Optional[Tuple[float, float]]:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT lat, lng, cached_at_utc FROM geocode_cache WHERE input_id = ?",
            (input_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        lat, lng, cached_at_utc = row
        try:
            cached_at = dt.datetime.fromisoformat(cached_at_utc)
            age = dt.datetime.now(dt.timezone.utc) - cached_at
            if age <= dt.timedelta(days=ttl_days):
                return float(lat), float(lng)
            else:
                # TTL expired — delete row
                conn.execute(
                    "DELETE FROM geocode_cache WHERE input_id = ?",
                    (input_id,),
                )
                conn.commit()
                return None
        except Exception:
            # If parsing fails, drop cache row to be safe.
            conn.execute("DELETE FROM geocode_cache WHERE input_id = ?", (input_id,))
            conn.commit()
            return None


# ------------------------------
# HTTP / API
# ------------------------------

_GEOCODE_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"


# Isolated for unit-test monkeypatching
def _http_get(url: str, params: Dict[str, Any], timeout: int) -> requests.Response:
    return requests.get(url, params=params, timeout=timeout)


# ------------------------------
# Logging (JSONL; thread-safe)
# ------------------------------


class JsonlLogger:
    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path:
            Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)

    def write(self, rec: Dict[str, Any]) -> None:
        if not self.path:
            return
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


# ------------------------------
# Geocoding with retry/backoff
# ------------------------------


def geocode_address_with_retry(
    input_id: str,
    address: str,
    api_key: Optional[str],
    retry: config_loader.RetryPolicy,
    logger: JsonlLogger,
    http_get=_http_get,
) -> Tuple[str, Optional[float], Optional[float], str, List[str]]:
    """Return (geocode_status, lat, lng, location_type, api_error_codes)."""
    api_error_codes: List[str] = []
    last_status = "UNKNOWN_ERROR"
    location_type = ""
    lat = None
    lng = None

    for attempt in range(1, retry.max_attempts + 1):
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            params = {
                "address": address,
                "key": api_key or "",
            }
            resp = http_get(_GEOCODE_ENDPOINT, params=params, timeout=15)
            http_status = resp.status_code
            body = {}
            try:
                body = resp.json() if resp.content else {}
            except Exception:
                body = {}

            last_status = str(body.get("status") or f"HTTP_{http_status}")

            if http_status == 200 and "status" in body:
                # Parse Google status
                status = body.get("status", "UNKNOWN_ERROR")
                results = body.get("results") or []
                if status == "OK" and results:
                    first = results[0]
                    geom = first.get("geometry") or {}
                    loc = geom.get("location") or {}
                    lt = geom.get("location_type") or ""

                    lat = float(loc.get("lat")) if "lat" in loc else None
                    lng = float(loc.get("lng")) if "lng" in loc else None
                    location_type = str(lt)
                    last_status = "OK"

                    # Log attempt
                    logger.write(
                        {
                            "ts": started,
                            "attempt": attempt,
                            "input_id": input_id,
                            "address": address,
                            "http_status": http_status,
                            "geocode_status": last_status,
                            "note": "OK",
                        }
                    )
                    return last_status, lat, lng, location_type, api_error_codes

                elif status in {
                    "ZERO_RESULTS",
                    "OVER_QUERY_LIMIT",
                    "REQUEST_DENIED",
                    "INVALID_REQUEST",
                    "UNKNOWN_ERROR",
                }:
                    # Do not treat ZERO_RESULTS as retryable; others may retry
                    logger.write(
                        {
                            "ts": started,
                            "attempt": attempt,
                            "input_id": input_id,
                            "address": address,
                            "http_status": http_status,
                            "geocode_status": status,
                            "note": "Non-OK status",
                        }
                    )
                    if status == "ZERO_RESULTS":
                        return status, None, None, "", api_error_codes
                    # For other statuses, fall through to backoff unless last attempt
                    last_status = status
                else:
                    # Unexpected body shape; mark as retryable
                    api_error_codes.append(f"PARSE_ERROR_ATTEMPT_{attempt}")
                    logger.write(
                        {
                            "ts": started,
                            "attempt": attempt,
                            "input_id": input_id,
                            "address": address,
                            "http_status": http_status,
                            "geocode_status": "PARSE_ERROR",
                        }
                    )
            else:
                # HTTP error
                code = f"HTTP_{http_status}"
                api_error_codes.append(code)
                logger.write(
                    {
                        "ts": started,
                        "attempt": attempt,
                        "input_id": input_id,
                        "address": address,
                        "http_status": http_status,
                        "geocode_status": code,
                    }
                )

        except Exception as e:
            code = f"EXC_{e.__class__.__name__}"
            api_error_codes.append(code)
            logger.write(
                {
                    "ts": started,
                    "attempt": attempt,
                    "input_id": input_id,
                    "address": address,
                    "http_status": None,
                    "geocode_status": code,
                }
            )

        # Backoff if not last attempt
        if attempt < retry.max_attempts:
            base = retry.base_seconds * (2 ** (attempt - 1))
            jitter = random.uniform(0, retry.jitter_seconds)
            time.sleep(base + jitter)

    # Exhausted retries
    return last_status, lat, lng, location_type, api_error_codes


# ------------------------------
# Orchestration
# ------------------------------


def _format_coord(value: Optional[float]) -> str:
    return f"{value:.6f}" if value is not None else ""


def geocode_rows(
    rows: List[Dict[str, str]],
    cfg: config_loader.Config,
    cache_db_path: str,
    log_path: Optional[str],
    http_get=_http_get,
) -> List[GeocodeResult]:
    api_key = cfg.api.get_google_maps_api_key()
    if not api_key:
        # Warn (non-fatal) so unit tests can run without env secrets.
        print(
            "WARNING: GOOGLE_MAPS_API_KEY is not set; live API calls will fail.",
            flush=True,
        )

    _ensure_cache_db(cache_db_path)
    logger = JsonlLogger(log_path)

    results_by_index: Dict[int, GeocodeResult] = {}
    lock = threading.Lock()

    def worker(ix: int, row: Dict[str, str]) -> None:
        input_id = row["input_id"]
        address = row["input_address_raw"]

        status, lat, lng, location_type, api_codes = geocode_address_with_retry(
            input_id=input_id,
            address=address,
            api_key=api_key,
            retry=cfg.retry,
            logger=logger,
            http_get=http_get,
        )

        # Cache ONLY lat/lng when available (policy-compliant)
        if status == "OK" and lat is not None and lng is not None:
            cache_set_latlng(cache_db_path, input_id, lat, lng)
        elif status != "OK":
            # Try cache to salvage coordinates (within TTL), but do NOT
            # misrepresent geocode_status. Keep status as obtained.
            cached = cache_get_latlng(
                cache_db_path, input_id, cfg.cache_policy.latlng_ttl_days
            )
            if cached:
                lat, lng = cached

        res = GeocodeResult(
            input_id=input_id,
            input_address_raw=address,
            geocode_status=status,
            lat=lat,
            lng=lng,
            location_type=location_type if status == "OK" else "",
            api_error_codes=api_codes,
        )
        with lock:
            results_by_index[ix] = res

    with ThreadPoolExecutor(max_workers=cfg.concurrency.workers) as pool:
        futures = []
        for ix, row in enumerate(rows):
            futures.append(pool.submit(worker, ix, row))
        # Wait for all to complete (exceptions bubble; none expected)
        for f in as_completed(futures):
            f.result()

    # Reconstruct results in input order deterministically
    return [results_by_index[i] for i in range(len(rows))]


def geocode_file(
    normalized_csv_path: str,
    output_csv_path: str,
    config_path: str,
    log_path: Optional[str] = None,
    cache_db_path: str = "data/cache/geocode_cache.sqlite",
    http_get=_http_get,
) -> int:
    """Run geocoding end-to-end.

    Returns number of processed rows.
    """
    cfg = config_loader.load_config(config_path)

    # Enforce cache TTL policy at config validation time (already in loader)
    if cfg.cache_policy.latlng_ttl_days > 30:
        raise ValueError("latlng_ttl_days must be <= 30")

    with open(normalized_csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    results = geocode_rows(rows, cfg, cache_db_path, log_path, http_get=http_get)

    # Ensure output directory exists
    out_dir = os.path.dirname(output_csv_path) or "."
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Deterministic write in input order; no timestamps in CSV
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(
            f_out,
            fieldnames=[
                "input_id",
                "input_address_raw",
                "geocode_status",
                "lat",
                "lng",
                "location_type",
                "api_error_codes",
            ],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "input_id": r.input_id,
                    "input_address_raw": r.input_address_raw,
                    "geocode_status": r.geocode_status,
                    "lat": _format_coord(r.lat),
                    "lng": _format_coord(r.lng),
                    "location_type": r.location_type,
                    "api_error_codes": "|".join(r.api_error_codes),
                }
            )

    return len(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Geocode addresses.")
    parser.add_argument(
        "--normalized", required=True, help="Path to data/normalized.csv"
    )
    parser.add_argument(
        "--output", required=True, help="Path to write data/geocode.csv"
    )
    parser.add_argument("--config", required=True, help="Path to config/config.yml")
    parser.add_argument(
        "--log",
        required=False,
        default="data/logs/geocode_api_log.jsonl",
        help="Path to JSONL API log (default: data/logs/geocode_api_log.jsonl)",
    )
    parser.add_argument(
        "--cache",
        required=False,
        default="data/cache/geocode_cache.sqlite",
        help="Path to SQLite cache (default: data/cache/geocode_cache.sqlite)",
    )
    args = parser.parse_args()

    count = geocode_file(
        normalized_csv_path=args.normalized,
        output_csv_path=args.output,
        config_path=args.config,
        log_path=args.log,
        cache_db_path=args.cache,
    )
    print(f"Geocoded {count} rows -> {args.output}")


if __name__ == "__main__":
    main()
