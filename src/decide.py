# src/decide.py
"""Decision engine & Maps URL generation.

Inputs (CSV; join key: input_id):
  - data/normalized.csv      (non_physical_flag)
  - data/geocode.csv         (input_address_raw, geocode_status, lat, lng, location_type, api_error_codes)
  - data/streetview_meta.csv (sv_metadata_status, sv_image_date, sv_stale_flag, api_error_codes)
  - data/footprints.csv      (footprint_within_m, footprint_present_flag)
  - data/validation.csv      (std_address, validation_ran_flag, validation_verdict, api_error_codes)

Outputs:
  - data/enhanced.csv (schema documented in docs/spec; see repository docs)
  - Optional QA summary JSON (counts per `final_flag` + deterministic run key)

Rules:
  - Apply explicit rule order and reason codes as documented in the spec.
  - Edge case: P.O. Boxes (non‑physical) are ALWAYS labeled NON_PHYSICAL_ADDRESS.
  - Per spec §12: any persistent API failure in upstream modules (after retries) must:
      * populate `api_error_codes`, and
      * short-circuit to NEEDS_HUMAN_REVIEW with reason `API_FAILURE`.

Compliance:
  - Generates only Google Maps **URLs** for human review (no scraping, no API here).
  - Uses only signals produced by official Google APIs in prior steps.

Determinism:
  - Column `run_timestamp_utc` defaults to the current UTC time (ISO‑8601).
  - To anchor for reproducible tests, set env RUN_ANCHOR_TIMESTAMP_UTC
    to an ISO‑8601 timestamp (e.g., "2025-01-01T00:00:00+00:00").

Idempotency:
  - We compute a run key `rk1|<sha256(input_csv_bytes || config_yaml_bytes)>` and include it
    in the summary JSON to uniquely identify a run configuration (§7.8).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import config_loader  # type: ignore
import urls  # type: ignore


# ------------------------------
# Helpers
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


def _parse_float(s: str | None) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _anchor_timestamp() -> str:
    """ISO‑8601 UTC timestamp, optionally anchored by env for reproducibility."""
    env = os.getenv("RUN_ANCHOR_TIMESTAMP_UTC")
    if env:
        # Accept 'Z' or offset forms
        s = env.strip().replace("Z", "+00:00")
        try:
            dt.datetime.fromisoformat(s)
            return s
        except Exception:
            pass
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _compute_run_key(input_csv_path: str, config_path: str) -> str:
    """Compute deterministic run key from input CSV bytes + config YAML bytes.

    Format: rk1|<sha256 hex of input_bytes + b'||' + config_bytes>
    """
    try:
        with open(input_csv_path, "rb") as f_in:
            input_bytes = f_in.read()
    except Exception:
        input_bytes = b""
    try:
        with open(config_path, "rb") as f_cfg:
            cfg_bytes = f_cfg.read()
    except Exception:
        cfg_bytes = b""
    payload = input_bytes + b"||" + cfg_bytes
    return "rk1|" + hashlib.sha256(payload).hexdigest()


# Controlled vocabulary ordering for deterministic reason code lists
_REASON_ORDER = [
    "NO_GEOCODE",
    "POSTAL_INVALID",
    "NON_PHYSICAL",
    "ROOFTOP",
    "LOW_PRECISION_GEOCODE",
    "FOOTPRINT_MATCH",
    "NO_FOOTPRINT",
    "SV_OK",
    "SV_ZERO_RESULTS",
    "SV_STALE",
    "API_FAILURE",  # persistent API errors after retries
]


@dataclass(frozen=True)
class EnhancedRow:
    input_id: str
    input_address_raw: str
    std_address: str
    geocode_status: str
    lat: str
    lng: str
    location_type: str
    sv_metadata_status: str
    sv_image_date: str
    sv_stale_flag: str  # "true"/"false"
    footprint_within_m: str
    footprint_present_flag: str  # "true"/"false"
    validation_ran_flag: str  # "true"/"false"
    validation_verdict: str  # VALID | UNCONFIRMED | INVALID | NOT_RUN
    non_physical_flag: str  # "true"/"false"
    google_maps_url: str
    final_flag: str  # VALID_LOCATION | INVALID_ADDRESS | LIKELY_EMPTY_LOT | NEEDS_HUMAN_REVIEW | NON_PHYSICAL_ADDRESS
    reason_codes: str  # pipe-delimited
    notes: str
    run_timestamp_utc: str
    api_error_codes: str


def _merge_api_error_codes(*lists: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for lst in lists:
        for code in lst:
            c = (code or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(c)
    return out


def _split_codes(s: str) -> List[str]:
    s = (s or "").strip()
    if not s:
        return []
    return [tok for tok in s.split("|") if tok.strip()]


def _decide_one(
    geo: Dict[str, str],
    norm: Dict[str, str],
    sv: Dict[str, str],
    fp: Dict[str, str],
    val: Dict[str, str],
) -> EnhancedRow:
    # Base fields
    input_id = geo.get("input_id", "")
    input_address_raw = geo.get("input_address_raw", "")
    geocode_status = (geo.get("geocode_status") or "").strip()
    lat_s = (geo.get("lat") or "").strip()
    lng_s = (geo.get("lng") or "").strip()
    location_type = (geo.get("location_type") or "").strip()
    geo_api_errs = _split_codes(geo.get("api_error_codes", ""))

    std_address = val.get("std_address", "")
    validation_ran_flag = _to_bool(val.get("validation_ran_flag", "false"))
    validation_verdict = (val.get("validation_verdict") or "NOT_RUN").strip()
    val_api_errs = _split_codes(val.get("api_error_codes", ""))

    sv_metadata_status = (sv.get("sv_metadata_status") or "").strip()
    sv_image_date = (sv.get("sv_image_date") or "").strip()
    sv_stale_flag_b = _to_bool(sv.get("sv_stale_flag", "false"))
    sv_api_errs = _split_codes(sv.get("api_error_codes", ""))

    footprint_within_m = (fp.get("footprint_within_m") or "").strip() or "-1"
    footprint_present_flag_b = _to_bool(fp.get("footprint_present_flag", "false"))

    non_physical_flag_b = _to_bool(norm.get("non_physical_flag", "false"))

    # Merge API error codes across modules (per spec §12)
    merged_api_errs = _merge_api_error_codes(geo_api_errs, sv_api_errs, val_api_errs)
    has_persistent_api_error = len(merged_api_errs) > 0

    # Prepare reason codes as a set (we'll render ordered later)
    reasons: set[str] = set()
    notes = ""

    # Derived signals -> reasons
    if geocode_status == "ZERO_RESULTS":
        reasons.add("NO_GEOCODE")
    if non_physical_flag_b:
        reasons.add("NON_PHYSICAL")
    if location_type == "ROOFTOP":
        reasons.add("ROOFTOP")
    elif geocode_status == "OK":
        # Non-rooftop precision but geocode successful
        reasons.add("LOW_PRECISION_GEOCODE")
    if footprint_present_flag_b:
        reasons.add("FOOTPRINT_MATCH")
    else:
        reasons.add("NO_FOOTPRINT")
    if sv_metadata_status == "OK":
        reasons.add("SV_OK")
    elif sv_metadata_status == "ZERO_RESULTS":
        reasons.add("SV_ZERO_RESULTS")
    if sv_stale_flag_b:
        reasons.add("SV_STALE")
    if has_persistent_api_error:
        reasons.add("API_FAILURE")

    if validation_ran_flag and validation_verdict == "INVALID":
        reasons.add("POSTAL_INVALID")

    # Optional note (compact)
    if sv_image_date:
        notes = f"SV date {sv_image_date}"

    # Decision order
    # Edge-case override: Non-physical always labeled NON_PHYSICAL_ADDRESS.
    if non_physical_flag_b:
        final_flag = "NON_PHYSICAL_ADDRESS"
    else:
        # Short-circuit for any persistent API errors across modules (spec §12)
        if has_persistent_api_error:
            final_flag = "NEEDS_HUMAN_REVIEW"
        else:
            # 1) Hard invalid
            if geocode_status == "ZERO_RESULTS":
                final_flag = "INVALID_ADDRESS"
            elif validation_ran_flag and validation_verdict == "INVALID":
                final_flag = "INVALID_ADDRESS"
            else:
                # 3) Auto-valid
                #   ROOFTOP AND (footprint_present OR (sv_status OK and NOT stale))
                if location_type == "ROOFTOP" and (
                    footprint_present_flag_b
                    or (sv_metadata_status == "OK" and not sv_stale_flag_b)
                ):
                    final_flag = "VALID_LOCATION"
                # 4) Likely empty lot
                elif (
                    location_type != "ROOFTOP"
                    and not footprint_present_flag_b
                    and (sv_metadata_status in {"OK", "ZERO_RESULTS"})
                    and not sv_stale_flag_b  # conservative when stale
                ):
                    final_flag = "LIKELY_EMPTY_LOT"
                else:
                    # 5) Needs human review (conflicts or anything else)
                    final_flag = "NEEDS_HUMAN_REVIEW"

    # Google Maps URL (prefer coordinates when available)
    lat_f = _parse_float(lat_s)
    lng_f = _parse_float(lng_s)
    fallback_addr = std_address or input_address_raw
    maps_url = urls.build_maps_search_url(
        address_fallback=fallback_addr, lat=lat_f, lng=lng_f
    )

    # Render booleans and reason codes with deterministic order
    reason_list = [r for r in _REASON_ORDER if r in reasons]
    reason_codes = "|".join(reason_list)

    return EnhancedRow(
        input_id=input_id,
        input_address_raw=input_address_raw,
        std_address=std_address,
        geocode_status=geocode_status,
        lat=lat_s,
        lng=lng_s,
        location_type=location_type,
        sv_metadata_status=sv_metadata_status,
        sv_image_date=sv_image_date,
        sv_stale_flag=_format_bool(sv_stale_flag_b),
        footprint_within_m=footprint_within_m,
        footprint_present_flag=_format_bool(footprint_present_flag_b),
        validation_ran_flag=_format_bool(validation_ran_flag),
        validation_verdict=validation_verdict,
        non_physical_flag=_format_bool(non_physical_flag_b),
        google_maps_url=maps_url,
        final_flag=final_flag,
        reason_codes=reason_codes,
        notes=notes,
        run_timestamp_utc=_anchor_timestamp(),
        api_error_codes="|".join(merged_api_errs),
    )


def _write_enhanced_csv(out_path: str, rows: List[EnhancedRow]) -> None:
    Path(os.path.dirname(out_path) or ".").mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "input_id",
        "input_address_raw",
        "std_address",
        "geocode_status",
        "lat",
        "lng",
        "location_type",
        "sv_metadata_status",
        "sv_image_date",
        "sv_stale_flag",
        "footprint_within_m",
        "footprint_present_flag",
        "validation_ran_flag",
        "validation_verdict",
        "non_physical_flag",
        "google_maps_url",
        "final_flag",
        "reason_codes",
        "notes",
        "run_timestamp_utc",
        "api_error_codes",
    ]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r.__dict__)


def _write_summary_json(summary_path: Optional[str], counts: Dict[str, int], run_key: str) -> None:
    if not summary_path:
        return
    Path(os.path.dirname(summary_path) or ".").mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({"run_key": run_key, "final_flag_counts": counts}, f, ensure_ascii=False, indent=2)


def run_decision(
    geocode_csv_path: str,
    svmeta_csv_path: str,
    footprints_csv_path: str,
    validation_csv_path: str,
    normalized_csv_path: str,
    output_csv_path: str,
    config_path: str,
    summary_json_path: Optional[str] = None,
) -> int:
    """Join inputs, apply rules, write enhanced.csv, and optional summary JSON.

    Returns the number of processed rows.
    """
    # Load config to ensure consistency / guardrails (not used directly here).
    _ = config_loader.load_config(config_path)

    geocode_rows = _read_csv_as_list(geocode_csv_path)
    sv_by_id = _read_csv_as_map(svmeta_csv_path, "input_id")
    fp_by_id = _read_csv_as_map(footprints_csv_path, "input_id")
    val_by_id = _read_csv_as_map(validation_csv_path, "input_id")
    norm_by_id = _read_csv_as_map(normalized_csv_path, "input_id")

    enhanced: List[EnhancedRow] = []
    counts: Dict[str, int] = {}

    for geo in geocode_rows:
        iid = geo.get("input_id", "")
        sv = sv_by_id.get(iid, {})
        fp = fp_by_id.get(iid, {})
        val = val_by_id.get(iid, {"std_address": "", "validation_ran_flag": "false", "validation_verdict": "NOT_RUN"})
        norm = norm_by_id.get(iid, {"non_physical_flag": "false", "input_address_raw": geo.get("input_address_raw", "")})

        row = _decide_one(geo, norm, sv, fp, val)
        enhanced.append(row)
        counts[row.final_flag] = counts.get(row.final_flag, 0) + 1

    _write_enhanced_csv(output_csv_path, enhanced)

    # Deterministic idempotency key to help correlate runs (§7.8)
    run_key = _compute_run_key(normalized_csv_path, config_path)
    _write_summary_json(summary_json_path, counts, run_key)
    return len(enhanced)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decision engine & Maps URL generation."
    )
    parser.add_argument("--geocode", required=True, help="Path to data/geocode.csv")
    parser.add_argument("--svmeta", required=True, help="Path to data/streetview_meta.csv")
    parser.add_argument("--footprints", required=True, help="Path to data/footprints.csv")
    parser.add_argument("--validation", required=True, help="Path to data/validation.csv")
    parser.add_argument("--normalized", required=True, help="Path to data/normalized.csv")
    parser.add_argument("--output", required=True, help="Path to write data/enhanced.csv")
    parser.add_argument("--config", required=True, help="Path to config/config.yml")
    parser.add_argument(
        "--summary",
        required=False,
        default="data/logs/decision_summary.json",
        help="Path to summary JSON with counts + run key (default: data/logs/decision_summary.json)",
    )
    args = parser.parse_args()

    n = run_decision(
        geocode_csv_path=args.geocode,
        svmeta_csv_path=args.svmeta,
        footprints_csv_path=args.footprints,
        validation_csv_path=args.validation,
        normalized_csv_path=args.normalized,
        output_csv_path=args.output,
        config_path=args.config,
        summary_json_path=args.summary,
    )
    print(f"Labeled {n} rows -> {args.output}")


if __name__ == "__main__":
    main()
