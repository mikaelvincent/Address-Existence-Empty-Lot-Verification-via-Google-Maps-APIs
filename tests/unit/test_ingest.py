import csv
import hashlib
import os
import pathlib
import sys

# Ensure we can import modules from src/
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
sys.path.append(str(SRC_DIR))

import normalize_addresses as nz  # type: ignore


def read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_single_line_ingest_and_po_box(tmp_path):
    cfg = str(REPO_ROOT / "config" / "config.yml")
    input_csv = REPO_ROOT / "tests" / "fixtures" / "single_line.csv"
    out_csv = tmp_path / "normalized.csv"

    count, mode = nz.normalize_file(str(input_csv), str(out_csv), cfg)
    assert count == 2
    assert mode == "single"

    rows = read_csv_rows(out_csv)
    # Row order preserved
    assert rows[0]["input_address_raw"].startswith("1600 Amphitheatre Pkwy")
    assert rows[0]["non_physical_flag"] == "false"

    # PO Box detection
    assert "P.O. Box 123" in rows[1]["input_address_raw"]
    assert rows[1]["non_physical_flag"] == "true"

    # Hash is stable (v1|<addr>)
    addr_raw = rows[0]["input_address_raw"]
    expected = hashlib.sha256(f"v1|{addr_raw}".encode("utf-8")).hexdigest()
    assert rows[0]["input_id"] == expected


def test_multi_field_ingest_and_us_default_country(tmp_path):
    cfg = str(REPO_ROOT / "config" / "config.yml")
    input_csv = REPO_ROOT / "tests" / "fixtures" / "multi_field.csv"
    out_csv = tmp_path / "normalized.csv"

    count, mode = nz.normalize_file(str(input_csv), str(out_csv), cfg)
    assert count == 2
    assert mode == "multi"

    rows = read_csv_rows(out_csv)

    # First row has no country but US ZIP; 'United States' should be appended
    assert rows[0]["non_physical_flag"] == "false"
    assert rows[0]["input_address_raw"].endswith("95014, United States")

    # Second row has explicit country & line2
    assert "Apt 5" in rows[1]["input_address_raw"]
    assert rows[1]["input_address_raw"].endswith("B2N 5E3, Canada")


def test_deterministic_rerun(tmp_path):
    cfg = str(REPO_ROOT / "config" / "config.yml")
    input_csv = REPO_ROOT / "tests" / "fixtures" / "po_box.csv"
    out_csv_1 = tmp_path / "normalized1.csv"
    out_csv_2 = tmp_path / "normalized2.csv"

    nz.normalize_file(str(input_csv), str(out_csv_1), cfg)
    nz.normalize_file(str(input_csv), str(out_csv_2), cfg)

    with open(out_csv_1, "rb") as f1, open(out_csv_2, "rb") as f2:
        assert f1.read() == f2.read(), "Re-runs should produce byte-identical outputs"
