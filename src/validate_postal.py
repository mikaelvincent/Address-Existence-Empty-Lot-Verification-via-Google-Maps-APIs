"""Conditional Address Validation.

Inputs (CSV; join key: input_id):
    * data/geocode.csv          (includes input_address_raw, location_type, place_id)
    * data/streetview_meta.csv  (metadata status/date/stale)
    * data/footprints.csv       (proximity flags)
    * data/normalized.csv       (non_physical_flag)

Validation runs when ANY of:
    * location_type in {RANGE_INTERPOLATED, GEOMETRIC_CENTER, APPROXIMATE}
    * footprint_present_flag == false
    * sv_metadata_status == ZERO_RESULTS
    * sv_stale_flag == true
    * non_physical_flag == true

API: Google Address Validation — v1:validateAddress

Output:
    * data/validation.csv with columns:
        input_id,
        std_address,
        validation_ran_flag,
        validation_verdict,
        validation_place_id,
        validation_lat,
        validation_lng,
        component_replaced_types,
        component_spell_corrected_types,
        unconfirmed_component_types,
        api_error_codes
      (one row per input_id; NOT_RUN when validation was skipped)

Compliance:
    * Uses only the official Address Validation API.
    * Sends freeform address via address.addressLines.
    * Secrets are read from environment via config_loader (no keys in repo).
    * Retries 429/5xx with exponential backoff (deterministic base; no jitter).

CLI:
    python src/validate_postal.py \
      --geocode data/geocode.csv \
      --svmeta data/streetview_meta.csv \
      --footprints data/footprints.csv \
      --normalized data/normalized.csv \
      --output data/validation.csv \
      --config config/config.yml \
      --log data/logs/address_validation_api_log.jsonl
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
import threading  # <-- thread-safe logging

import requests

import config_loader  # type: ignore


# ------------------------------
# Data model
# ------------------------------


@dataclass(frozen=True)
class ValidationResult:
    input_id: str
    std_address: str
    validation_ran_flag: bool
    validation_verdict: str  # VALID | UNCONFIRMED | INVALID | NOT_RUN
    validation_place_id: str
    validation_lat: str
    validation_lng: str
    component_replaced_types: List[str]
    component_spell_corrected_types: List[str]
    unconfirmed_component_types: List[str]
    api_error_codes: List[str]


# ------------------------------
# I/O helpers
# ------------------------------


def _read_csv_as_list(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _read_csv_as_map(path: str, key: str) -> Dict[str, Dict[str, str]]:
    rows = _read_csv_as_list(path)
    return {r.get(key, ""): r for r in rows if r.get(key, "")}


def _to_bool(s: str | None) -> bool:
    return str(s).strip().lower() == "true"


def _format_bool(b: bool) -> str:
    return "true" if b else "false"


def _format_coord(value: Optional[float]) -> str:
    return f"{value:.6f}" if value is not None else ""


# ------------------------------
# Address Validation API
# ------------------------------

_ENDPOINT = "https://addressvalidation.googleapis.com/v1:validateAddress"


def _http_post(
    url: str, params: Dict[str, Any], json_body: Dict[str, Any], timeout: int
) -> requests.Response:
    return requests.post(url, params=params, json=json_body, timeout=timeout)


class JsonlLogger:
    """Thread-safe JSONL logger (guards writes with a lock)."""
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


def _granularity_rank(g: Optional[str]) -> int:
    """Order for validationGranularity to compare '>= PREMISE'."""
    g = (g or "").upper()
    order = {
        "GRANULARITY_UNSPECIFIED": 0,
        "OTHER": 0,
        "ROUTE": 1,
        "BLOCK": 2,
        "PREMISE_PROXIMITY": 3,
        "PREMISE": 4,
        "SUB_PREMISE": 5,
    }
    return order.get(g, 0)


def _derive_verdict(v: Optional[Dict[str, Any]]) -> str:
    """Map Google verdict to simplified enum.

    VALID when:
      addressComplete==true AND hasUnconfirmedComponents==false
      AND validationGranularity >= PREMISE.
    INVALID when:
      addressComplete==false AND (hasUnconfirmedComponents==true OR granularity too coarse/unspecified).
    Otherwise UNCONFIRMED.
    """
    if not v or not isinstance(v, dict):
        return "UNCONFIRMED"

    address_complete = bool(v.get("addressComplete", False))
    has_unconfirmed = bool(v.get("hasUnconfirmedComponents", False))
    granularity = str(v.get("validationGranularity", "") or "")
    rank = _granularity_rank(granularity)

    if (
        address_complete
        and not has_unconfirmed
        and rank >= _granularity_rank("PREMISE")
    ):
        return "VALID"

    if (not address_complete) and (
        has_unconfirmed or rank <= _granularity_rank("OTHER")
    ):
        return "INVALID"

    return "UNCONFIRMED"


def _pick_std_address(result_obj: Dict[str, Any]) -> str:
    """Extract a standardized address string from API result if present."""
    addr_obj = (result_obj or {}).get("address") or {}
    # Prefer Google's formattedAddress when available
    formatted = addr_obj.get("formattedAddress")
    if isinstance(formatted, str) and formatted.strip():
        return formatted.strip()

    # Fallback: join addressLines if provided
    postal = addr_obj.get("postalAddress") or {}
    lines = postal.get("addressLines") or []
    if isinstance(lines, list) and lines:
        return ", ".join([str(x) for x in lines if str(x).strip()])

    return ""


def _extract_components(result_obj: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    """Return (replaced_types, spell_corrected_types, unconfirmed_types)."""
    addr_obj = (result_obj or {}).get("address") or {}
    comps = addr_obj.get("addressComponents") or []
    replaced: List[str] = []
    spell: List[str] = []
    unconfirmed: List[str] = []

    for c in comps:
        ctype = str(c.get("componentType") or c.get("type") or "").upper()
        if not ctype:
            continue
        if bool(c.get("replaced", False)):
            replaced.append(ctype)
        if bool(c.get("spellCorrected", False)):
            spell.append(ctype)
        conf = str(c.get("confirmationLevel") or "").upper()
        # Treat anything other than 'CONFIRMED' as unconfirmed
        if conf and conf != "CONFIRMED":
            unconfirmed.append(ctype)

    return replaced, spell, unconfirmed


def _extract_geocode(result_obj: Dict[str, Any]) -> Tuple[str, Optional[float], Optional[float]]:
    """Return (place_id, lat, lng) from result.geocode (best effort across schema variants)."""
    g = (result_obj or {}).get("geocode") or {}
    place_id = str(g.get("placeId") or g.get("place_id") or "")  # robust to casing
    lat = None
    lng = None

    loc = g.get("location") or g.get("latLng") or {}
    try:
        # Common variants:
        lat = float(
            loc.get("latitude")
            if "latitude" in loc
            else loc.get("lat")
            if "lat" in loc
            else (loc.get("latLng") or {}).get("latitude")
        )
    except Exception:
        lat = None
    try:
        lng = float(
            loc.get("longitude")
            if "longitude" in loc
            else loc.get("lng")
            if "lng" in loc
            else (loc.get("latLng") or {}).get("longitude")
        )
    except Exception:
        lng = None

    return place_id, lat, lng


def validate_one(
    input_id: str,
    address_raw: str,
    api_key: Optional[str],
    retry: config_loader.RetryPolicy,
    logger: JsonlLogger,
    http_post=_http_post,
) -> Tuple[
    str,  # std_address
    str,  # simplified_verdict
    str,  # validation_place_id
    Optional[float],  # validation_lat
    Optional[float],  # validation_lng
    List[str],  # replaced_types
    List[str],  # spell_corrected_types
    List[str],  # unconfirmed_types
    List[str],  # api_error_codes
]:
    """Call Address Validation API; return detailed signals.

    Error classification policy (spec §12):
    - Any transport/server/exception failures must NOT become 'INVALID'.
    - Such failures return simplified_verdict='UNCONFIRMED' and are surfaced via api_error_codes.
    """
    std_address = ""
    simplified = "UNCONFIRMED"
    api_errs: List[str] = []
    val_place_id = ""
    val_lat: Optional[float] = None
    val_lng: Optional[float] = None
    replaced_types: List[str] = []
    spell_types: List[str] = []
    unconfirmed_types: List[str] = []

    params = {"key": api_key or ""}
    body = {
        "address": {
            "addressLines": [address_raw],
        },
    }

    last_status = "UNKNOWN_ERROR"

    for attempt in range(1, retry.max_attempts + 1):
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            resp = http_post(_ENDPOINT, params=params, json_body=body, timeout=20)
            http_status = resp.status_code
            payload = {}
            try:
                payload = resp.json() if resp.content else {}
            except Exception:
                payload = {}

            # Success path
            if http_status == 200 and "result" in payload:
                result = payload.get("result", {})
                verdict = result.get("verdict", {}) or {}
                std_address = _pick_std_address(result)
                simplified = _derive_verdict(verdict)
                replaced_types, spell_types, unconfirmed_types = _extract_components(result)
                val_place_id, val_lat, val_lng = _extract_geocode(result)
                last_status = "OK"

                logger.write(
                    {
                        "ts": started,
                        "input_id": input_id,
                        "attempt": attempt,
                        "http_status": http_status,
                        "api_status": "OK",
                        "simplified_verdict": simplified,
                        "validation_place_id": val_place_id,
                    }
                )
                return (
                    std_address,
                    simplified,
                    val_place_id,
                    val_lat,
                    val_lng,
                    replaced_types,
                    spell_types,
                    unconfirmed_types,
                    api_errs,
                )

            # Error responses may include top-level "error" with "status"
            err_status = (payload.get("error", {}) or {}).get("status")
            if err_status:
                last_status = str(err_status)
                api_errs.append(f"ADDRVAL_{last_status}")
            else:
                last_status = f"HTTP_{http_status}"
                if http_status != 200:
                    api_errs.append(f"ADDRVAL_HTTP_{http_status}")

            logger.write(
                {
                    "ts": started,
                    "input_id": input_id,
                    "attempt": attempt,
                    "http_status": http_status,
                    "api_status": last_status,
                }
            )

        except Exception as e:
            last_status = f"EXC_{e.__class__.__name__}"
            api_errs.append(f"ADDRVAL_EXC_{e.__class__.__name__}")
            logger.write(
                {
                    "ts": started,
                    "input_id": input_id,
                    "attempt": attempt,
                    "http_status": None,
                    "api_status": last_status,
                }
            )

        # Backoff if not final attempt (deterministic; no jitter)
        if attempt < retry.max_attempts:
            base = retry.base_seconds * (2 ** (attempt - 1))
            time.sleep(base)

    # Exhausted retries — per spec, treat as UNCONFIRMED; errors surfaced via api_error_codes
    return (
        std_address,
        "UNCONFIRMED",
        val_place_id,
        val_lat,
        val_lng,
        replaced_types,
        spell_types,
        unconfirmed_types,
        api_errs,
    )


# ------------------------------
# Orchestration
# ------------------------------


def _should_validate(
    location_type: str,
    footprint_present_flag: bool,
    sv_metadata_status: str,
    sv_stale_flag: bool,
    non_physical_flag: bool,
) -> bool:
    if location_type in {"RANGE_INTERPOLATED", "GEOMETRIC_CENTER", "APPROXIMATE"}:
        return True
    if not footprint_present_flag:
        return True
    if sv_metadata_status == "ZERO_RESULTS":
        return True
    if sv_stale_flag:
        return True
    if non_physical_flag:
        return True
    return False


def run_validation(
    geocode_csv_path: str,
    svmeta_csv_path: str,
    footprints_csv_path: str,
    normalized_csv_path: str,
    output_csv_path: str,
    config_path: str,
    log_path: Optional[str] = None,
    http_post=_http_post,
) -> int:
    """Decide which rows to validate, call API as needed, and write CSV."""
    cfg = config_loader.load_config(config_path)

    # Load inputs
    geocode_rows = _read_csv_as_list(geocode_csv_path)
    svmeta_by_id = _read_csv_as_map(svmeta_csv_path, "input_id")
    footprints_by_id = _read_csv_as_map(footprints_csv_path, "input_id")
    normalized_by_id = _read_csv_as_map(normalized_csv_path, "input_id")

    api_key = cfg.api.get_address_validation_api_key()
    if not api_key:
        print(
            "WARNING: GOOGLE_ADDRESS_VALIDATION_API_KEY is not set; live API calls will fail.",
            flush=True,
        )

    logger = JsonlLogger(log_path)
    results_by_index: Dict[int, ValidationResult] = {}

    # Prepare tasks (only for rows that need validation)
    tasks: List[Tuple[int, Dict[str, str]]] = []
    for ix, row in enumerate(geocode_rows):
        iid = row.get("input_id", "")
        g_loc_type = (row.get("location_type") or "").strip()
        fp_row = footprints_by_id.get(iid, {})
        sv_row = svmeta_by_id.get(iid, {})
        norm_row = normalized_by_id.get(iid, {})

        fp_present = _to_bool(fp_row.get("footprint_present_flag", "false"))
        sv_status = (sv_row.get("sv_metadata_status") or "").strip()
        sv_stale = _to_bool(sv_row.get("sv_stale_flag", "false"))
        non_phys = _to_bool(norm_row.get("non_physical_flag", "false"))

        if _should_validate(
            g_loc_type,
            fp_present,
            sv_status,
            sv_stale,
            non_phys,
        ):
            tasks.append((ix, row))
        else:
            # Pre-populate NOT_RUN
            results_by_index[ix] = ValidationResult(
                input_id=iid,
                std_address="",
                validation_ran_flag=False,
                validation_verdict="NOT_RUN",
                validation_place_id="",
                validation_lat="",
                validation_lng="",
                component_replaced_types=[],
                component_spell_corrected_types=[],
                unconfirmed_component_types=[],
                api_error_codes=[],
            )

    # Execute validations concurrently
    def worker(ix: int, row: Dict[str, str]) -> Tuple[int, ValidationResult]:
        iid = row.get("input_id", "")
        address_raw = row.get("input_address_raw", "")
        (
            std_addr,
            simplified,
            v_place_id,
            v_lat,
            v_lng,
            repl_types,
            spell_types,
            unconf_types,
            errs,
        ) = validate_one(
            input_id=iid,
            address_raw=address_raw,
            api_key=api_key,
            retry=cfg.retry,
            logger=logger,
            http_post=http_post,
        )
        return ix, ValidationResult(
            input_id=iid,
            std_address=std_addr,
            validation_ran_flag=True,
            validation_verdict=simplified,
            validation_place_id=v_place_id,
            validation_lat=_format_coord(v_lat),
            validation_lng=_format_coord(v_lng),
            component_replaced_types=repl_types,
            component_spell_corrected_types=spell_types,
            unconfirmed_component_types=unconf_types,
            api_error_codes=errs,
        )

    with ThreadPoolExecutor(max_workers=cfg.concurrency.workers) as pool:
        futures = [pool.submit(worker, ix, row) for ix, row in tasks]
        for fut in as_completed(futures):
            ix, res = fut.result()
            results_by_index[ix] = res

    # Ensure output directory exists
    Path(os.path.dirname(output_csv_path) or ".").mkdir(parents=True, exist_ok=True)

    # Deterministic write in geocode input order
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(
            f_out,
            fieldnames=[
                "input_id",
                "std_address",
                "validation_ran_flag",
                "validation_verdict",
                "validation_place_id",
                "validation_lat",
                "validation_lng",
                "component_replaced_types",
                "component_spell_corrected_types",
                "unconfirmed_component_types",
                "api_error_codes",
            ],
        )
        writer.writeheader()
        for i in range(len(geocode_rows)):
            r = results_by_index.get(
                i,
                ValidationResult(
                    input_id=geocode_rows[i].get("input_id", ""),
                    std_address="",
                    validation_ran_flag=False,
                    validation_verdict="NOT_RUN",
                    validation_place_id="",
                    validation_lat="",
                    validation_lng="",
                    component_replaced_types=[],
                    component_spell_corrected_types=[],
                    unconfirmed_component_types=[],
                    api_error_codes=[],
                ),
            )
            writer.writerow(
                {
                    "input_id": r.input_id,
                    "std_address": r.std_address,
                    "validation_ran_flag": _format_bool(r.validation_ran_flag),
                    "validation_verdict": r.validation_verdict,
                    "validation_place_id": r.validation_place_id,
                    "validation_lat": r.validation_lat,
                    "validation_lng": r.validation_lng,
                    "component_replaced_types": "|".join(r.component_replaced_types),
                    "component_spell_corrected_types": "|".join(r.component_spell_corrected_types),
                    "unconfirmed_component_types": "|".join(r.unconfirmed_component_types),
                    "api_error_codes": "|".join(r.api_error_codes),
                }
            )

    return len(geocode_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Conditional Address Validation."
    )
    parser.add_argument("--geocode", required=True, help="Path to data/geocode.csv")
    parser.add_argument(
        "--svmeta", required=True, help="Path to data/streetview_meta.csv"
    )
    parser.add_argument(
        "--footprints", required=True, help="Path to data/footprints.csv"
    )
    parser.add_argument(
        "--normalized", required=True, help="Path to data/normalized.csv"
    )
    parser.add_argument(
        "--output", required=True, help="Path to write data/validation.csv"
    )
    parser.add_argument("--config", required=True, help="Path to config/config.yml")
    parser.add_argument(
        "--log",
        required=False,
        default="data/logs/address_validation_api_log.jsonl",
        help="Path to JSONL API log (default: data/logs/address_validation_api_log.jsonl)",
    )
    args = parser.parse_args()

    n = run_validation(
        geocode_csv_path=args.geocode,
        svmeta_csv_path=args.svmeta,
        footprints_csv_path=args.footprints,
        normalized_csv_path=args.normalized,
        output_csv_path=args.output,
        config_path=args.config,
        log_path=args.log,
    )
    print(f"Validated (conditional) {n} rows -> {args.output}")


if __name__ == "__main__":
    main()
