import csv
import pathlib
import sys

# Ensure src/ is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import review_pack as rp  # type: ignore


def write_enhanced(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
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
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_review_pack_outputs(tmp_path):
    cfg = str(REPO_ROOT / "config" / "config.yml")

    enhanced_rows = [
        {
            "input_id": "a1",
            "input_address_raw": "10 Valid Way",
            "std_address": "",
            "geocode_status": "OK",
            "lat": "1.0",
            "lng": "1.0",
            "location_type": "ROOFTOP",
            "sv_metadata_status": "OK",
            "sv_image_date": "2024-01",
            "sv_stale_flag": "false",
            "footprint_within_m": "5",
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
            "input_id": "a2",
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
            "api_error_codes": "",
        },
        {
            "input_id": "a3",
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
        {
            "input_id": "a4",
            "input_address_raw": "P.O. Box 1",
            "std_address": "",
            "geocode_status": "ZERO_RESULTS",
            "lat": "",
            "lng": "",
            "location_type": "",
            "sv_metadata_status": "",
            "sv_image_date": "",
            "sv_stale_flag": "false",
            "footprint_within_m": "-1",
            "footprint_present_flag": "false",
            "validation_ran_flag": "false",
            "validation_verdict": "NOT_RUN",
            "non_physical_flag": "true",
            "google_maps_url": "https://www.google.com/maps/search/?api=1&query=P.O.%20Box%201",
            "final_flag": "NON_PHYSICAL_ADDRESS",
            "reason_codes": "NON_PHYSICAL",
            "notes": "",
            "run_timestamp_utc": "2025-01-01T00:00:00+00:00",
            "api_error_codes": "",
        },
    ]

    enhanced = tmp_path / "enhanced.csv"
    write_enhanced(enhanced, enhanced_rows)

    qout = tmp_path / "review_queue.csv"
    ltout = tmp_path / "review_log_template.csv"
    rmd = tmp_path / "reviewer_rubric.md"
    rpdf = tmp_path / "reviewer_rubric.pdf"

    n = rp.run_review_pack(
        enhanced_csv_path=str(enhanced),
        queue_csv_path=str(qout),
        log_template_csv_path=str(ltout),
        rubric_md_path=str(rmd),
        rubric_pdf_path=str(rpdf),
        config_path=str(REPO_ROOT / "config" / "config.yml"),
    )
    assert n == 2  # rows a2 (LIKELY_EMPTY_LOT) and a3 (NEEDS_HUMAN_REVIEW)

    q_rows = read_csv_rows(qout)
    ids = [r["input_id"] for r in q_rows]
    assert ids == ["a2", "a3"]
    # Essential columns present
    for col in ["google_maps_url", "location_type", "sv_metadata_status", "reason_codes"]:
        assert col in q_rows[0]

    lt_rows = read_csv_rows(ltout)
    assert lt_rows[0]["input_id"] == "a2"
    assert "review_decision" in lt_rows[0]
    assert "reviewer_initials" in lt_rows[0]
    assert "review_notes" in lt_rows[0]

    with open(rmd, "r", encoding="utf-8") as f:
        rubric = f.read()
    assert "Street View" in rubric
    assert "LIKELY_EMPTY_LOT" in rubric
