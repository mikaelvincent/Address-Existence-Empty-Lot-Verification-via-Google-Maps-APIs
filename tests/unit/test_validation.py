import csv
import json
import pathlib
import sys

# Ensure src/ is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import validate_postal as vp  # type: ignore


def write_csv(path, headers, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)


class DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = json.dumps(self._payload).encode("utf-8")

    def json(self):
        return self._payload


def test_validation_triggers_and_verdicts(tmp_path, monkeypatch):
    # Build geocode.csv (source of order + input_address_raw)
    geocode_rows = [
        {
            "input_id": "id1",
            "input_address_raw": "10 Valid Way, Testville, TS 12345",
            "geocode_status": "OK",
            "lat": "1.0",
            "lng": "1.0",
            "location_type": "ROOFTOP",  # should NOT trigger on loc_type
            "api_error_codes": "",
        },
        {
            "input_id": "id2",
            "input_address_raw": "20 Fuzzy Rd, Testville, TS 12345",
            "geocode_status": "OK",
            "lat": "1.0",
            "lng": "1.0",
            "location_type": "RANGE_INTERPOLATED",  # trigger
            "api_error_codes": "",
        },
        {
            "input_id": "id3",
            "input_address_raw": "PMB 99, 30 Main St, Testville, TS 12345",
            "geocode_status": "OK",
            "lat": "1.0",
            "lng": "1.0",
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

    # Street View metadata — make row1 OK with fresh date; row2/3 irrelevant for trigger
    sv_rows = [
        {
            "input_id": "id1",
            "sv_metadata_status": "OK",
            "sv_image_date": "2024-01",
            "sv_stale_flag": "false",
        },
        {
            "input_id": "id2",
            "sv_metadata_status": "OK",
            "sv_image_date": "2020-01",
            "sv_stale_flag": "true",
        },  # also trigger by stale
        {
            "input_id": "id3",
            "sv_metadata_status": "OK",
            "sv_image_date": "2024-01",
            "sv_stale_flag": "false",
        },
    ]
    svpath = tmp_path / "streetview_meta.csv"
    write_csv(
        svpath,
        ["input_id", "sv_metadata_status", "sv_image_date", "sv_stale_flag"],
        sv_rows,
    )

    # Footprints — id1 present, id2 present, id3 absent (trigger)
    fp_rows = [
        {
            "input_id": "id1",
            "footprint_within_m": "6",
            "footprint_present_flag": "true",
        },
        {
            "input_id": "id2",
            "footprint_within_m": "18",
            "footprint_present_flag": "true",
        },
        {
            "input_id": "id3",
            "footprint_within_m": "-1",
            "footprint_present_flag": "false",
        },
    ]
    fppath = tmp_path / "footprints.csv"
    write_csv(
        fppath, ["input_id", "footprint_within_m", "footprint_present_flag"], fp_rows
    )

    # Normalized — id3 is non-physical (trigger)
    norm_rows = [
        {
            "input_id": "id1",
            "input_address_raw": geocode_rows[0]["input_address_raw"],
            "non_physical_flag": "false",
        },
        {
            "input_id": "id2",
            "input_address_raw": geocode_rows[1]["input_address_raw"],
            "non_physical_flag": "false",
        },
        {
            "input_id": "id3",
            "input_address_raw": geocode_rows[2]["input_address_raw"],
            "non_physical_flag": "true",
        },
    ]
    npath = tmp_path / "normalized.csv"
    write_csv(npath, ["input_id", "input_address_raw", "non_physical_flag"], norm_rows)

    # Stub API: id2 => VALID; id3 => INVALID
    def stub_post(url, params, json_body, timeout):
        lines = json_body.get("address", {}).get("addressLines", [])
        addr = lines[0] if lines else ""
        if "20 Fuzzy Rd" in addr:
            payload = {
                "result": {
                    "verdict": {
                        "addressComplete": True,
                        "hasUnconfirmedComponents": False,
                        "validationGranularity": "PREMISE",
                    },
                    "address": {
                        "formattedAddress": "20 Fuzzy Rd, Testville, TS 12345, USA"
                    },
                }
            }
            return DummyResponse(200, payload)
        elif "PMB 99" in addr:
            payload = {
                "result": {
                    "verdict": {
                        "addressComplete": False,
                        "hasUnconfirmedComponents": True,
                        "validationGranularity": "ROUTE",
                    },
                    "address": {
                        "formattedAddress": "PMB 99, 30 Main St, Testville, TS 12345"
                    },
                }
            }
            return DummyResponse(200, payload)
        return DummyResponse(500, {"error": {"status": "INTERNAL"}})

    monkeypatch.setattr(vp, "_http_post", stub_post)
    # Avoid real sleep
    monkeypatch.setattr(vp.time, "sleep", lambda *_a, **_k: None)

    out_csv = tmp_path / "validation.csv"
    log_path = tmp_path / "addr_val_log.jsonl"
    cfg = str(REPO_ROOT / "config" / "config.yml")

    n = vp.run_validation(
        geocode_csv_path=str(gpath),
        svmeta_csv_path=str(svpath),
        footprints_csv_path=str(fppath),
        normalized_csv_path=str(npath),
        output_csv_path=str(out_csv),
        config_path=cfg,
        log_path=str(log_path),
        http_post=stub_post,
    )
    assert n == 3

    with open(out_csv, "r", encoding="utf-8", newline="") as f:
        rows_out = list(csv.DictReader(f))

    # id1: should NOT run (no triggers)
    assert rows_out[0]["input_id"] == "id1"
    assert rows_out[0]["validation_ran_flag"] == "false"
    assert rows_out[0]["validation_verdict"] == "NOT_RUN"
    assert rows_out[0]["std_address"] == ""

    # id2: VALID
    assert rows_out[1]["input_id"] == "id2"
    assert rows_out[1]["validation_ran_flag"] == "true"
    assert rows_out[1]["validation_verdict"] == "VALID"
    assert "20 Fuzzy Rd" in rows_out[1]["std_address"]

    # id3: INVALID
    assert rows_out[2]["input_id"] == "id3"
    assert rows_out[2]["validation_ran_flag"] == "true"
    assert rows_out[2]["validation_verdict"] == "INVALID"
    assert "PMB 99" in rows_out[2]["std_address"]

    # Log should have at least two lines (rows 2 & 3 validated)
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) >= 2
