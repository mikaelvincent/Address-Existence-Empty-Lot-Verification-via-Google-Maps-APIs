import pathlib
import sys
import csv

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


def test_streaming_geojson_path_variants(tmp_path):
    # Minimal geocodes near two small polygons in the fixture
    gpath = tmp_path / "geocode.csv"
    rows = [
        {
            "input_id": "id1",
            "input_address_raw": "Near A",
            "geocode_status": "OK",
            "lat": "37.422476",
            "lng": "-122.084250",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
        {
            "input_id": "id2",
            "input_address_raw": "Near B",
            "geocode_status": "OK",
            "lat": "37.484722",
            "lng": "-122.148333",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
    ]
    write_geocode_csv(gpath, rows)

    fixture = REPO_ROOT / "tests" / "fixtures" / "footprints_sample.geojson"
    out_csv = tmp_path / "footprints.csv"
    cfg = str(REPO_ROOT / "config" / "config.yml")

    # Force streaming even for tiny file; we just want to exercise streaming code path
    n = fp.run_footprints(
        geocode_csv_path=str(gpath),
        output_csv_path=str(out_csv),
        config_path=cfg,
        footprint_paths=[str(fixture)],
        log_path=None,
        cell_deg=0.01,
        prefer_streaming=True,
        stream_threshold_mb=0,  # force streaming
        progress_every=0,  # keep test output quiet
        on_stream_fail="fallback",
    )
    assert n == 2

    with open(out_csv, "r", encoding="utf-8", newline="") as f:
        out_rows = list(csv.DictReader(f))

    assert out_rows[0]["footprint_present_flag"] == "true"
    assert out_rows[1]["footprint_present_flag"] == "true"
