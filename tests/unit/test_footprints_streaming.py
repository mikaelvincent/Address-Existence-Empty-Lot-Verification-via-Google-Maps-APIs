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


def test_streaming_parser_with_featurecollection(tmp_path):
    # Force streaming by setting a zero threshold
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
        }
    ]
    write_geocode_csv(gpath, rows)

    fixture = REPO_ROOT / "tests" / "fixtures" / "footprints_sample.geojson"
    out_csv = tmp_path / "footprints.csv"
    cfg = str(REPO_ROOT / "config" / "config.yml")

    # This should go through the streaming code path
    n = fp.run_footprints(
        geocode_csv_path=str(gpath),
        output_csv_path=str(out_csv),
        config_path=cfg,
        footprint_paths=[str(fixture)],
        log_path=None,
        cell_deg=0.01,
        prefer_streaming=True,
        stream_threshold_mb=0,  # force streaming even for small file
        on_stream_fail="fallback",  # be lenient in CI
    )
    assert n == 1
    with open(out_csv, "r", encoding="utf-8", newline="") as f:
        out_rows = list(csv.DictReader(f))
    assert out_rows[0]["input_id"] == "id1"
