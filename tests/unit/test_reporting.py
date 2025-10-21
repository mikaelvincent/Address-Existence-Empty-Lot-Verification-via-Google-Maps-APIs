import csv
import json
import pathlib
import sys

# Ensure src/ is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import reporting as rp  # type: ignore


def _write_enhanced(path, rows):
    headers = [
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
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_reviews(path, rows):
    headers = ["input_id", "review_decision", "reviewer_initials", "review_notes"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _read_csv(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_reporting_overrides_and_outputs(tmp_path):
    cfg = str(REPO_ROOT / "config" / "config.yml")

    # Prepare minimal enhanced.csv with three rows
    enhanced_rows = [
        {
            "input_id": "r1",
            "input_address_raw": "10 Valid Way",
            "std_address": "",
            "geocode_status": "OK",
            "lat": "1.0",
            "lng": "1.0",
            "location_type": "ROOFTOP",
            "sv_metadata_status": "OK",
            "sv_image_date": "2024-01",
            "sv_stale_flag": "false",
            "footprint_within_m": "4",
            "footprint_present_flag": "true",
            "validation_ran_flag": "false",
            "validation_verdict": "NOT_RUN",
            "non_physical_flag": "false",
            "google_maps_url": "https://www.google.com/maps/search/?api=1&query=1.000000%2C1.000000",
            "final_flag": "VALID_LOCATION",
            "reason_codes": "ROOFTOP|FOOTPRINT_MATCH|SV_OK",
            "notes": "SV date 2024-01",
            "run_timestamp_utc": "2025-01-01T00:00:00+00:00",
            "api_error_codes": "",
        },
        {
            "input_id": "r2",
            "input_address_raw": "20 Maybe Rd",
            "std_address": "",
            "geocode_status": "OK",
            "lat": "2.0",
            "lng": "2.0",
            "location_type": "APPROXIMATE",
            "sv_metadata_status": "OK",
            "sv_image_date": "2024-01",
            "sv_stale_flag": "false",
            "footprint_within_m": "-1",
            "footprint_present_flag": "false",
            "validation_ran_flag": "true",
            "validation_verdict": "UNCONFIRMED",
            "non_physical_flag": "false",
            "google_maps_url": "https://www.google.com/maps/search/?api=1&query=2.000000%2C2.000000",
            "final_flag": "LIKELY_EMPTY_LOT",
            "reason_codes": "LOW_PRECISION_GEOCODE|NO_FOOTPRINT|SV_OK",
            "notes": "SV date 2024-01",
            "run_timestamp_utc": "2025-01-01T00:00:00+00:00",
            "api_error_codes": "HTTP_500|PARSE_ERROR_ATTEMPT_1",
        },
        {
            "input_id": "r3",
            "input_address_raw": "30 Conflict Ave",
            "std_address": "",
            "geocode_status": "OK",
            "lat": "3.0",
            "lng": "3.0",
            "location_type": "ROOFTOP",
            "sv_metadata_status": "OK",
            "sv_image_date": "2010-01",
            "sv_stale_flag": "true",
            "footprint_within_m": "-1",
            "footprint_present_flag": "false",
            "validation_ran_flag": "false",
            "validation_verdict": "NOT_RUN",
            "non_physical_flag": "false",
            "google_maps_url": "https://www.google.com/maps/search/?api=1&query=3.000000%2C3.000000",
            "final_flag": "NEEDS_HUMAN_REVIEW",
            "reason_codes": "ROOFTOP|NO_FOOTPRINT|SV_STALE",
            "notes": "SV date 2010-01",
            "run_timestamp_utc": "2025-01-01T00:00:00+00:00",
            "api_error_codes": "",
        },
    ]
    enh = tmp_path / "enhanced.csv"
    _write_enhanced(enh, enhanced_rows)

    # Completed review log: r2 -> CONFIRM_INVALID, r3 -> CONFIRM_VALID (AB)
    reviews = [
        {"input_id": "r2", "review_decision": "CONFIRM_INVALID", "reviewer_initials": "CD", "review_notes": ""},
        {"input_id": "r3", "review_decision": "CONFIRM_VALID", "reviewer_initials": "AB", "review_notes": "looks good"},
    ]
    rev = tmp_path / "review_log_completed.csv"
    _write_reviews(rev, reviews)

    final_out = tmp_path / "final_enhanced.csv"
    md = tmp_path / "run_report.md"
    pdf = tmp_path / "run_report.pdf"
    jlog = tmp_path / "final_decisions.jsonl"

    n = rp.run_reporting(
        enhanced_csv_path=str(enh),
        final_csv_out=str(final_out),
        report_md_out=str(md),
        report_pdf_out=str(pdf),
        decisions_jsonl_out=str(jlog),
        config_path=str(REPO_ROOT / "config" / "config.yml"),
        review_log_completed_path=str(rev),
    )
    assert n == 3

    # Final CSV checks
    rows_out = _read_csv(final_out)
    by_id = {r["input_id"]: r for r in rows_out}
    assert by_id["r2"]["final_flag"] == "INVALID_ADDRESS"
    assert by_id["r3"]["final_flag"] == "VALID_LOCATION"
    assert "Human override: CONFIRM_VALID (AB)" in by_id["r3"]["notes"]

    # Markdown report exists and contains counts
    with open(md, "r", encoding="utf-8") as f:
        report_text = f.read()
    assert "Final counts by label" in report_text
    assert "INVALID_ADDRESS" in report_text
    assert "VALID_LOCATION" in report_text

    # JSONL decisions
    with open(jlog, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 3
    srcs = {rec["input_id"]: rec["source"] for rec in lines}
    assert srcs["r1"] == "AUTO"
    assert srcs["r2"] == "HUMAN"
    assert srcs["r3"] == "HUMAN"
