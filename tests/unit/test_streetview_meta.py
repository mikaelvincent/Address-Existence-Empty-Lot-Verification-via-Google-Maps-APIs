import csv
import json
import os
import pathlib
import sys

import pytest

# Ensure src/ is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import streetview_meta as svm  # type: ignore


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


class DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = json.dumps(self._payload).encode("utf-8")

    def json(self):
        return self._payload


def test_streetview_metadata_happy_and_edges(tmp_path, monkeypatch):
    # Anchor deterministically
    monkeypatch.setenv("SV_ANCHOR_DATE_UTC", "2025-01-01")

    # Build geocode.csv with four rows
    gpath = tmp_path / "geocode.csv"
    rows = [
        {
            # Row 1: coords -> OK with date
            "input_id": "id1",
            "input_address_raw": "1600 Amphitheatre Pkwy, Mountain View, CA 94043",
            "geocode_status": "OK",
            "lat": "37.422476",
            "lng": "-122.084250",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
        {
            # Row 2: coords -> OK without date
            "input_id": "id2",
            "input_address_raw": "1 Hacker Way, Menlo Park, CA 94025",
            "geocode_status": "OK",
            "lat": "37.484722",
            "lng": "-122.148333",
            "location_type": "ROOFTOP",
            "api_error_codes": "",
        },
        {
            # Row 3: coords -> ZERO_RESULTS
            "input_id": "id3",
            "input_address_raw": "Some rural rd",
            "geocode_status": "OK",
            "lat": "40.000000",
            "lng": "-75.000000",
            "location_type": "APPROXIMATE",
            "api_error_codes": "",
        },
        {
            # Row 4: no coords
            "input_id": "id4",
            "input_address_raw": "P.O. Box 123, Austin, TX 78701",
            "geocode_status": "ZERO_RESULTS",
            "lat": "",
            "lng": "",
            "location_type": "",
            "api_error_codes": "",
        },
    ]
    write_geocode_csv(gpath, rows)

    # Stub HTTP based on location
    def stub_get(url, params, timeout):
        loc = params.get("location", "")
        if loc == "37.422476,-122.08425":
            return DummyResponse(200, {"status": "OK", "date": "2015-07"})
        if loc == "37.484722,-122.148333":
            return DummyResponse(200, {"status": "OK"})  # no date
        if loc == "40.0,-75.0" or loc == "40.000000,-75.000000":
            return DummyResponse(200, {"status": "ZERO_RESULTS"})
        return DummyResponse(500, {"status": "UNKNOWN_ERROR"})

    monkeypatch.setattr(svm, "_http_get", stub_get)
    # Avoid real sleeping
    monkeypatch.setattr(svm.time, "sleep", lambda *_a, **_k: None)

    out_csv = tmp_path / "streetview_meta.csv"
    log_path = tmp_path / "sv_log.jsonl"
    cfg = str(REPO_ROOT / "config" / "config.yml")

    count = svm.run_sv_metadata(
        geocode_csv_path=str(gpath),
        output_csv_path=str(out_csv),
        config_path=cfg,
        log_path=str(log_path),
        http_get=stub_get,
    )
    assert count == 4

    # Read and assert
    with open(out_csv, "r", encoding="utf-8", newline="") as f:
        rows_out = list(csv.DictReader(f))

    assert rows_out[0]["sv_metadata_status"] == "OK"
    assert rows_out[0]["sv_image_date"] == "2015-07"
    assert rows_out[0]["sv_stale_flag"] == "true"  # > 7 years before 2025-01-01

    assert rows_out[1]["sv_metadata_status"] == "OK"
    assert rows_out[1]["sv_image_date"] == ""
    assert rows_out[1]["sv_stale_flag"] == "true"  # OK but no date => stale

    assert rows_out[2]["sv_metadata_status"] == "ZERO_RESULTS"
    assert rows_out[2]["sv_image_date"] == ""
    assert rows_out[2]["sv_stale_flag"] == "false"

    assert rows_out[3]["sv_metadata_status"] == ""
    assert rows_out[3]["sv_image_date"] == ""
    assert rows_out[3]["sv_stale_flag"] == "false"

    # Log should have at least three lines (rows with coordinates)
    with open(log_path, "r", encoding="utf-8") as f:
        log_lines = [json.loads(line) for line in f if line.strip()]
    assert len(log_lines) >= 3
