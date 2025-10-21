# tests/unit/test_decide.py
import csv
import pathlib
import sys

# Ensure src/ is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import decide as dc  # type: ignore


def write_csv(path, headers, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def read_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_decision_engine_rules_and_urls(tmp_path):
    cfg = str(REPO_ROOT / "config" / "config.yml")

    # Geocode (source of truth order)
    geocode_rows = [
        # 0: Hard invalid (ZERO_RESULTS)
        {
            "input_id": "id0",
            "input_address_raw": "10 Nowhere St, Nocity, NC 00000",
            "geocode_status": "ZERO_RESULTS",
            "lat": "",
            "lng": "",
            "location_type": "",
            "api_error_codes": "",
        },
        # 1: Non-physical (should override everything)
        {
            "input_id": "id1",
            "input_address_raw": "P.O. Box 55, Austin, TX 78701",
            "geocode_status": "OK",
            "lat": "30.267150",
            "lng": "-97.743060",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
        # 2: Auto-valid via footprint + rooftop (stale SV allowed)
        {
            "input_id": "id2",
            "input_address_raw": "1600 Amphitheatre Pkwy, Mountain View, CA 94043",
            "geocode_status": "OK",
            "lat": "37.422476",
            "lng": "-122.084250",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
        # 3: Likely empty lot (approximate + no footprint + SV OK fresh)
        {
            "input_id": "id3",
            "input_address_raw": "Some dirt road",
            "geocode_status": "OK",
            "lat": "40.000000",
            "lng": "-75.000000",
            "location_type": "APPROXIMATE",
            "api_error_codes": "",
        },
        # 4: Needs review (rooftop + no footprint + stale SV)
        {
            "input_id": "id4",
            "input_address_raw": "Conflicting signals place",
            "geocode_status": "OK",
            "lat": "37.484722",
            "lng": "-122.148333",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
    ]
    gpath = tmp_path / "geocode.csv"
    write_csv(
        gpath,
        [
            "input_id",
            "input_address_raw",
            "geocode_status",
            "lat",
            "lng",
            "location_type",
            "api_error_codes",
        ],
        geocode_rows,
    )

    # Street View metadata
    sv_rows = [
        {"input_id": "id0", "sv_metadata_status": "", "sv_image_date": "", "sv_stale_flag": "false"},
        {"input_id": "id1", "sv_metadata_status": "OK", "sv_image_date": "2010-01", "sv_stale_flag": "true"},
        {"input_id": "id2", "sv_metadata_status": "OK", "sv_image_date": "2015-07", "sv_stale_flag": "true"},
        {"input_id": "id3", "sv_metadata_status": "OK", "sv_image_date": "2024-01", "sv_stale_flag": "false"},
        {"input_id": "id4", "sv_metadata_status": "OK", "sv_image_date": "2012-01", "sv_stale_flag": "true"},
    ]
    svpath = tmp_path / "streetview_meta.csv"
    write_csv(
        svpath, ["input_id", "sv_metadata_status", "sv_image_date", "sv_stale_flag"], sv_rows
    )

    # Footprints
    fp_rows = [
        {"input_id": "id0", "footprint_within_m": "-1", "footprint_present_flag": "false"},
        {"input_id": "id1", "footprint_within_m": "-1", "footprint_present_flag": "false"},
        {"input_id": "id2", "footprint_within_m": "9", "footprint_present_flag": "true"},
        {"input_id": "id3", "footprint_within_m": "-1", "footprint_present_flag": "false"},
        {"input_id": "id4", "footprint_within_m": "-1", "footprint_present_flag": "false"},
    ]
    fppath = tmp_path / "footprints.csv"
    write_csv(fppath, ["input_id", "footprint_within_m", "footprint_present_flag"], fp_rows)

    # Validation (not critical here, but present)
    val_rows = [
        {"input_id": "id0", "std_address": "", "validation_ran_flag": "false", "validation_verdict": "NOT_RUN"},
        {"input_id": "id1", "std_address": "", "validation_ran_flag": "true", "validation_verdict": "INVALID"},
        {"input_id": "id2", "std_address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043", "validation_ran_flag": "false", "validation_verdict": "NOT_RUN"},
        {"input_id": "id3", "std_address": "", "validation_ran_flag": "false", "validation_verdict": "NOT_RUN"},
        {"input_id": "id4", "std_address": "", "validation_ran_flag": "false", "validation_verdict": "NOT_RUN"},
    ]
    vpath = tmp_path / "validation.csv"
    write_csv(
        vpath, ["input_id", "std_address", "validation_ran_flag", "validation_verdict"], val_rows
    )

    # Normalized (non-physical)
    n_rows = [
        {"input_id": "id0", "input_address_raw": geocode_rows[0]["input_address_raw"], "non_physical_flag": "false"},
        {"input_id": "id1", "input_address_raw": geocode_rows[1]["input_address_raw"], "non_physical_flag": "true"},
        {"input_id": "id2", "input_address_raw": geocode_rows[2]["input_address_raw"], "non_physical_flag": "false"},
        {"input_id": "id3", "input_address_raw": geocode_rows[3]["input_address_raw"], "non_physical_flag": "false"},
        {"input_id": "id4", "input_address_raw": geocode_rows[4]["input_address_raw"], "non_physical_flag": "false"},
    ]
    npath = tmp_path / "normalized.csv"
    write_csv(npath, ["input_id", "input_address_raw", "non_physical_flag"], n_rows)

    out_csv = tmp_path / "enhanced.csv"
    summary_json = tmp_path / "summary.json"

    count = dc.run_decision(
        geocode_csv_path=str(gpath),
        svmeta_csv_path=str(svpath),
        footprints_csv_path=str(fppath),
        validation_csv_path=str(vpath),
        normalized_csv_path=str(npath),
        output_csv_path=str(out_csv),
        config_path=cfg,
        summary_json_path=str(summary_json),
    )
    assert count == 5

    rows_out = read_rows(out_csv)
    # id0: INVALID_ADDRESS due to NO_GEOCODE
    assert rows_out[0]["input_id"] == "id0"
    assert rows_out[0]["final_flag"] == "INVALID_ADDRESS"
    assert "NO_GEOCODE" in rows_out[0]["reason_codes"]
    assert "query=10+Nowhere+St" in rows_out[0]["google_maps_url"]

    # id1: NON_PHYSICAL_ADDRESS (edge-case override)
    assert rows_out[1]["input_id"] == "id1"
    assert rows_out[1]["final_flag"] == "NON_PHYSICAL_ADDRESS"
    assert "NON_PHYSICAL" in rows_out[1]["reason_codes"]
    # URL should prefer coordinates when present
    assert "query=30.267150%2C-97.743060" in rows_out[1]["google_maps_url"]

    # id2: VALID_LOCATION via ROOFTOP + FOOTPRINT_MATCH
    assert rows_out[2]["input_id"] == "id2"
    assert rows_out[2]["final_flag"] == "VALID_LOCATION"
    assert "ROOFTOP" in rows_out[2]["reason_codes"]
    assert "FOOTPRINT_MATCH" in rows_out[2]["reason_codes"]
    assert "query=37.422476%2C-122.084250" in rows_out[2]["google_maps_url"]

    # id3: LIKELY_EMPTY_LOT (approximate + no footprint + SV OK fresh)
    assert rows_out[3]["input_id"] == "id3"
    assert rows_out[3]["final_flag"] == "LIKELY_EMPTY_LOT"
    rc = rows_out[3]["reason_codes"]
    assert "LOW_PRECISION_GEOCODE" in rc and "NO_FOOTPRINT" in rc and "SV_OK" in rc

    # id4: NEEDS_HUMAN_REVIEW (rooftop + no footprint + stale SV)
    assert rows_out[4]["input_id"] == "id4"
    assert rows_out[4]["final_flag"] == "NEEDS_HUMAN_REVIEW"
    rc = rows_out[4]["reason_codes"]
    assert "ROOFTOP" in rc and "NO_FOOTPRINT" in rc and "SV_STALE" in rc
