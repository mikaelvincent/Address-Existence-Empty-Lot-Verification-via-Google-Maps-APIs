"""Street View metadata integration (Sprint 3)

- Reads data/geocode.csv (input_id, geocode_status, lat, lng)
- Calls Google Street View Static API **metadata** endpoint for rows with coordinates
- Parses: sv_metadata_status, sv_image_date, sv_stale_flag
- Writes:
    * data/streetview_meta.csv
    * data/logs/streetview_meta_api_log.jsonl (API attempt logs, PII-safe)
- Deterministic: preserves input order; no timestamps in CSV output

Compliance notes:
- This module queries **metadata** only; it does **not** request Street View images.
- Metadata requests require an API key and are **no-charge**; only image loads are billed.
- When passing `location=<lat,lng>`, Google automatically searches for a panorama within ~50 m;
  this radius is not configurable on the metadata endpoint.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import config_loader  # type: ignore


@dataclass(frozen=True)
class StreetViewMetaResult:
    input_id: str
    sv_metadata_status: str
    sv_image_date: str
    sv_stale_flag: bool


_SV_METADATA_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview/metadata"


# Isolated for unit-test monkeypatching
def _http_get(url: str, params: Dict[str, Any], timeout: int) -> requests.Response:
    return requests.get(url, params=params, timeout=timeout)


class JsonlLogger:
    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        if self.path:
            Path(os.path.dirname(self.path) or ".").mkdir(parents=True, exist_ok=True)

    def write(self, rec: Dict[str, Any]) -> None:
        if not self.path:
            return
        line = json.dumps(rec, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _parse_sv_date(date_str: Optional[str]) -> Optional[dt.date]:
    """Parse 'YYYY-MM' or 'YYYY' into a date.

    If only a year is provided, assume Dec 31 of that year (conservative: less likely stale).
    """
    if not date_str:
        return None
    date_str = str(date_str).strip()
    try:
        if len(date_str) == 7 and date_str[4] == "-":  # YYYY-MM
            y = int(date_str[:4])
            m = int(date_str[5:7])
            return dt.date(y, m, 1)
        if len(date_str) == 4 and date_str.isdigit():
            y = int(date_str)
            return dt.date(y, 12, 31)
    except Exception:
        return None
    return None


def _anchor_date() -> dt.date:
    """Return deterministic anchor date for staleness evaluation.

    If the environment variable SV_ANCHOR_DATE_UTC (YYYY-MM-DD) is set, use it. Otherwise, use current UTC date.
    """
    env = os.getenv("SV_ANCHOR_DATE_UTC")
    if env:
        try:
            return dt.date.fromisoformat(env)
        except Exception:
            pass
    return dt.datetime.now(dt.timezone.utc).date()


def _is_stale(status: str, date_str: Optional[str], stale_years: int) -> bool:
    """Compute staleness per spec:
    - True if image date older than `stale_years`
    - OR if date is missing but status == OK
    """
    if status != "OK":
        return False
    d = _parse_sv_date(date_str)
    if d is None:
        return True  # OK but date missing
    anchor = _anchor_date()
    # Age in years (floating)
    age_days = (anchor - d).days
    age_years = age_days / 365.2425
    return age_years >= stale_years


def _format_bool(b: bool) -> str:
    return "true" if b else "false"


def fetch_sv_metadata_for_coord(
    input_id: str,
    lat: float,
    lng: float,
    api_key: Optional[str],
    retry: config_loader.RetryPolicy,
    logger: JsonlLogger,
    http_get=_http_get,
) -> Tuple[str, str]:
    """Return (sv_metadata_status, sv_image_date)."""
    last_status = "UNKNOWN_ERROR"
    image_date = ""

    for attempt in range(1, retry.max_attempts + 1):
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            params = {
                "location": f"{lat},{lng}",
                "key": api_key or "",
            }
            resp = http_get(_SV_METADATA_ENDPOINT, params=params, timeout=15)
            http_status = resp.status_code
            body = {}
            try:
                body = resp.json() if resp.content else {}
            except Exception:
                body = {}

            status = str(body.get("status", f"HTTP_{http_status}"))
            last_status = status

            if http_status == 200 and "status" in body:
                if status == "OK":
                    image_date = str(body.get("date") or "")
                    logger.write(
                        {
                            "ts": started,
                            "attempt": attempt,
                            "input_id": input_id,
                            "location": params["location"],
                            "http_status": http_status,
                            "sv_metadata_status": status,
                            "note": "OK",
                        }
                    )
                    return status, image_date
                elif status in {
                    "ZERO_RESULTS",
                    "NOT_FOUND",
                    "OVER_QUERY_LIMIT",
                    "REQUEST_DENIED",
                    "INVALID_REQUEST",
                    "UNKNOWN_ERROR",
                }:
                    logger.write(
                        {
                            "ts": started,
                            "attempt": attempt,
                            "input_id": input_id,
                            "location": params["location"],
                            "http_status": http_status,
                            "sv_metadata_status": status,
                            "note": "Non-OK status",
                        }
                    )
                    if status in {"ZERO_RESULTS", "INVALID_REQUEST"}:
                        # Not retryable
                        return status, ""
                    # Otherwise retry on next loop
                else:
                    # Unexpected body shape; retry
                    logger.write(
                        {
                            "ts": started,
                            "attempt": attempt,
                            "input_id": input_id,
                            "location": params["location"],
                            "http_status": http_status,
                            "sv_metadata_status": "PARSE_ERROR",
                        }
                    )
            else:
                # HTTP error; possibly retry
                logger.write(
                    {
                        "ts": started,
                        "attempt": attempt,
                        "input_id": input_id,
                        "location": f"{lat},{lng}",
                        "http_status": http_status,
                        "sv_metadata_status": f"HTTP_{http_status}",
                    }
                )
        except Exception as e:
            logger.write(
                {
                    "ts": started,
                    "attempt": attempt,
                    "input_id": input_id,
                    "location": f"{lat},{lng}",
                    "http_status": None,
                    "sv_metadata_status": f"EXC_{e.__class__.__name__}",
                }
            )

        # Backoff if not last attempt (deterministic; no jitter)
        if attempt < retry.max_attempts:
            base = retry.base_seconds * (2 ** (attempt - 1))
            time.sleep(base)

    return last_status, image_date


def run_sv_metadata(
    geocode_csv_path: str,
    output_csv_path: str,
    config_path: str,
    log_path: Optional[str] = None,
    http_get=_http_get,
) -> int:
    """Read geocodes and write Street View metadata CSV.

    Returns number of rows.
    """
    cfg = config_loader.load_config(config_path)
    api_key = cfg.api.get_google_maps_api_key()
    if not api_key:
        print(
            "WARNING: GOOGLE_MAPS_API_KEY is not set; live API calls will fail.",
            flush=True,
        )

    # Read input
    with open(geocode_csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Prepare logger and output dir
    logger = JsonlLogger(log_path)
    out_dir = os.path.dirname(output_csv_path) or "."
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Worker for concurrency
    def worker(ix: int, row: Dict[str, str]) -> Tuple[int, StreetViewMetaResult]:
        input_id = row.get("input_id", "")
        geocode_status = row.get("geocode_status", "")
        lat_s = (row.get("lat") or "").strip()
        lng_s = (row.get("lng") or "").strip()

        status = ""
        date_s = ""
        stale = False

        if geocode_status == "OK" and lat_s and lng_s:
            try:
                lat = float(lat_s)
                lng = float(lng_s)
                status, date_s = fetch_sv_metadata_for_coord(
                    input_id=input_id,
                    lat=lat,
                    lng=lng,
                    api_key=api_key,
                    retry=cfg.retry,
                    logger=logger,
                    http_get=http_get,
                )
                stale = _is_stale(status, date_s, cfg.thresholds.stale_years)
            except Exception:
                status = "UNKNOWN_ERROR"
                date_s = ""
                stale = False

        return ix, StreetViewMetaResult(
            input_id=input_id,
            sv_metadata_status=status,
            sv_image_date=date_s,
            sv_stale_flag=stale,
        )

    # Execute with ThreadPoolExecutor, preserving order
    results_by_index: Dict[int, StreetViewMetaResult] = {}
    with ThreadPoolExecutor(max_workers=cfg.concurrency.workers) as pool:
        futures = []
        for ix, row in enumerate(rows):
            futures.append(pool.submit(worker, ix, row))
        for fut in as_completed(futures):
            ix, res = fut.result()
            results_by_index[ix] = res

    # Deterministic write (input order)
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(
            f_out,
            fieldnames=[
                "input_id",
                "sv_metadata_status",
                "sv_image_date",
                "sv_stale_flag",
            ],
        )
        writer.writeheader()
        for i in range(len(rows)):
            r = results_by_index[i]
            writer.writerow(
                {
                    "input_id": r.input_id,
                    "sv_metadata_status": r.sv_metadata_status,
                    "sv_image_date": r.sv_image_date,
                    "sv_stale_flag": _format_bool(r.sv_stale_flag),
                }
            )

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attach Street View metadata to geocoded rows (Sprint 3)."
    )
    parser.add_argument("--geocode", required=True, help="Path to data/geocode.csv")
    parser.add_argument(
        "--output", required=True, help="Path to write data/streetview_meta.csv"
    )
    parser.add_argument("--config", required=True, help="Path to config/config.yml")
    parser.add_argument(
        "--log",
        required=False,
        default="data/logs/streetview_meta_api_log.jsonl",
        help="Path to JSONL API log (default: data/logs/streetview_meta_api_log.jsonl)",
    )
    args = parser.parse_args()

    count = run_sv_metadata(
        geocode_csv_path=args.geocode,
        output_csv_path=args.output,
        config_path=args.config,
        log_path=args.log,
    )
    print(f"Enriched {count} rows -> {args.output}")


if __name__ == "__main__":
    main()
