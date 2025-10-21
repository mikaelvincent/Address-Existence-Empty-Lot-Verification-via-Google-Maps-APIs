import csv
import pathlib
import sys

# Ensure src/ is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import footprints as fp  # type: ignore


def write_geocode_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
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
        for r in rows:
            writer.writerow(r)


def test_footprints_proximity_sample(tmp_path):
    # Geocoded rows: two near polygons, one far
    gpath = tmp_path / "geocode.csv"
    rows = [
        {
            "input_id": "id1",
            "input_address_raw": "Near Googleplex",
            "geocode_status": "OK",
            "lat": "37.422476",
            "lng": "-122.084250",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
        {
            "input_id": "id2",
            "input_address_raw": "Far away",
            "geocode_status": "OK",
            "lat": "40.000000",
            "lng": "-75.000000",
            "location_type": "APPROXIMATE",
            "api_error_codes": "",
        },
        {
            "input_id": "id3",
            "input_address_raw": "Near Menlo Park",
            "geocode_status": "OK",
            "lat": "37.484722",
            "lng": "-122.148333",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
    ]
    write_geocode_csv(gpath, rows)

    # Use sample GeoJSON fixture with small squares around the first and third coords
    fixture = REPO_ROOT / "tests" / "fixtures" / "footprints_sample.geojson"
    out_csv = tmp_path / "footprints.csv"
    log_path = tmp_path / "footprints_log.jsonl"
    cfg = str(REPO_ROOT / "config" / "config.yml")

    n = fp.run_footprints(
        geocode_csv_path=str(gpath),
        output_csv_path=str(out_csv),
        config_path=cfg,
        footprint_paths=[str(fixture)],
        log_path=str(log_path),
        cell_deg=0.01,
    )
    assert n == 3

    # Validate outputs
    with open(out_csv, "r", encoding="utf-8", newline="") as f:
        out_rows = list(csv.DictReader(f))

    # id1: Should be present within a small distance
    assert out_rows[0]["input_id"] == "id1"
    assert out_rows[0]["footprint_present_flag"] == "true"
    d1 = int(out_rows[0]["footprint_within_m"])
    assert 0 <= d1 <= 25

    # id2: Far from sample polygons
    assert out_rows[1]["input_id"] == "id2"
    assert out_rows[1]["footprint_present_flag"] == "false"
    assert out_rows[1]["footprint_within_m"] == "-1"

    # id3: Should be present
    assert out_rows[2]["input_id"] == "id3"
    assert out_rows[2]["footprint_present_flag"] == "true"
    d3 = int(out_rows[2]["footprint_within_m"])
    assert 0 <= d3 <= 25
