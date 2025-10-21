import csv
import json
import os
import pathlib
import sys
import types

import pytest

# Ensure src/ is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import normalize_addresses as nz  # type: ignore
import geocode as gc  # type: ignore


def read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = json.dumps(self._payload).encode("utf-8")

    def json(self):
        return self._payload


def test_geocode_ok_and_zero_results(tmp_path, monkeypatch):
    # Prepare normalized.csv via Sprint 1 path
    cfg = str(REPO_ROOT / "config" / "config.yml")
    input_csv = REPO_ROOT / "tests" / "fixtures" / "single_line.csv"
    normalized = tmp_path / "normalized.csv"
    nz.normalize_file(str(input_csv), str(normalized), cfg)

    # Stub HTTP: first address OK, second ZERO_RESULTS (P.O. Box)
    def stub_get(url, params, timeout):
        addr = params.get("address", "")
        if "1600 Amphitheatre" in addr:
            payload = {
                "status": "OK",
                "results": [
                    {
                        "geometry": {
                            "location": {"lat": 37.422476, "lng": -122.08425},
                            "location_type": "ROOFTOP",
                        }
                    }
                ],
            }
            return DummyResponse(200, payload)
        else:
            payload = {"status": "ZERO_RESULTS", "results": []}
            return DummyResponse(200, payload)

    # Patch HTTP
    monkeypatch.setattr(gc, "_http_get", stub_get)

    out_csv = tmp_path / "geocode.csv"
    log_path = tmp_path / "geocode_log.jsonl"
    cache_db = tmp_path / "cache.sqlite"

    count = gc.geocode_file(
        normalized_csv_path=str(normalized),
        output_csv_path=str(out_csv),
        config_path=cfg,
        log_path=str(log_path),
        cache_db_path=str(cache_db),
        http_get=stub_get,
    )
    assert count == 2

    rows = read_csv_rows(out_csv)
    # Row 0: OK
    assert rows[0]["geocode_status"] == "OK"
    assert rows[0]["location_type"] == "ROOFTOP"
    assert rows[0]["lat"] == "37.422476"
    assert rows[0]["lng"] == "-122.084250"
    # Row 1: ZERO_RESULTS
    assert rows[1]["geocode_status"] == "ZERO_RESULTS"
    assert rows[1]["location_type"] == ""
    assert rows[1]["lat"] == ""
    assert rows[1]["lng"] == ""

    # Log contains at least two entries
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) >= 2
    statuses = {l["geocode_status"] for l in lines}
    assert "OK" in statuses or "ZERO_RESULTS" in statuses


def test_geocode_retry_backoff(tmp_path, monkeypatch):
    cfg = str(REPO_ROOT / "config" / "config.yml")
    # Build normalized from a single-row CSV created on the fly
    src_csv = tmp_path / "in.csv"
    with open(src_csv, "w", encoding="utf-8", newline="") as f:
        f.write("full_address\n1 Hacker Way, Menlo Park, CA 94025\n")
    normalized = tmp_path / "normalized.csv"
    nz.normalize_file(str(src_csv), str(normalized), cfg)

    # Stub HTTP that fails first, then succeeds
    call_counter = {"n": 0}

    def stub_get(url, params, timeout):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return DummyResponse(500, {"status": "UNKNOWN_ERROR"})
        else:
            return DummyResponse(
                200,
                {
                    "status": "OK",
                    "results": [
                        {
                            "geometry": {
                                "location": {"lat": 37.484722, "lng": -122.148333},
                                "location_type": "ROOFTOP",
                            }
                        }
                    ],
                },
            )

    # Remove actual sleeping for tests
    monkeypatch.setattr(gc.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gc, "_http_get", stub_get)

    out_csv = tmp_path / "geocode.csv"
    log_path = tmp_path / "geocode_log.jsonl"
    cache_db = tmp_path / "cache.sqlite"

    count = gc.geocode_file(
        normalized_csv_path=str(normalized),
        output_csv_path=str(out_csv),
        config_path=cfg,
        log_path=str(log_path),
        cache_db_path=str(cache_db),
        http_get=stub_get,
    )
    assert count == 1

    rows = read_csv_rows(out_csv)
    assert rows[0]["geocode_status"] == "OK"
    assert rows[0]["location_type"] == "ROOFTOP"
    assert rows[0]["lat"] == "37.484722"
    assert rows[0]["lng"] == "-122.148333"

    # Ensure two attempts were logged
    with open(log_path, "r", encoding="utf-8") as f:
        attempts = [json.loads(line) for line in f if line.strip()]
    assert len(attempts) >= 2
    assert any(a.get("geocode_status") == "HTTP_500" for a in attempts)
    assert any(a.get("geocode_status") == "OK" for a in attempts)
