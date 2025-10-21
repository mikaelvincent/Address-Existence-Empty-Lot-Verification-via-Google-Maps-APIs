"""CSV ingestion & normalization (Sprint 1)

Outputs: data/normalized.csv with columns:
- input_id: stable SHA-256 hex of canonicalized address parts (v1|<input_address_raw>)
- input_address_raw: normalized address string used later for geocoding
- non_physical_flag: boolean (true/false)

Schema detection:
- If 'full_address' is present -> single-line mode
- Else multi-field mode with any subset of:
  address_line1, address_line2, city, region, postal_code, country

Normalization:
- Trim and collapse internal whitespace for each part
- Preserve casing and punctuation in the resulting `input_address_raw`
- If `country` is missing but `postal_code` matches US ZIP -> default to "United States"
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Local import (when run as a script from src/)
import config_loader  # type: ignore


NON_PHYSICAL_RE = re.compile(
    r"(?i)\b("
    r"P\.?\s*O\.?\s*BOX"
    r"|POST\s+OFFICE\s+BOX"
    r"|LOCKBOX"
    r"|PMB"
    r"|PRIVATE\s+MAILBOX"
    r"|SUITE\s*#?\s*[\dA-Z]+\s+AT\s+UPS\s+STORE"
    r")\b"
)

US_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def collapse_ws(s: str) -> str:
    """Collapse internal whitespace to a single space; strip leading/trailing."""
    return " ".join(s.split())


def is_us_zip(s: str | None) -> bool:
    return bool(s) and bool(US_ZIP_RE.match(s.strip()))


def canonical_join(parts: Iterable[str]) -> str:
    """Join non-empty parts with ', ' after collapsing whitespace in each."""
    cleaned = [collapse_ws(p) for p in parts if p is not None and collapse_ws(p) != ""]
    return ", ".join(cleaned)


def derive_country(
    raw_country: str | None, postal_code: str | None, default_country_if_us_zip: str
) -> str | None:
    if raw_country and collapse_ws(raw_country) != "":
        return collapse_ws(raw_country)
    if is_us_zip(postal_code):
        return default_country_if_us_zip
    return None


def build_input_address_raw(
    row: Dict[str, str], default_country_if_us_zip: str, mode: str
) -> str:
    if mode == "single":
        return collapse_ws(row.get("full_address", "") or "")
    # multi-field
    line1 = row.get("address_line1", "")
    line2 = row.get("address_line2", "")
    city = row.get("city", "")
    region = row.get("region", "")
    postal_code = row.get("postal_code", "")
    country = derive_country(
        row.get("country", ""), postal_code, default_country_if_us_zip
    )
    parts = [line1, line2, city, region, postal_code, country or ""]
    return canonical_join(parts)


def compute_input_id(address_raw: str) -> str:
    # Version prefix allows future hashing changes without breaking idempotency contracts.
    payload = f"v1|{address_raw}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def detect_schema(fieldnames: List[str]) -> str:
    if "full_address" in fieldnames:
        return "single"
    allowed = {
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country",
    }
    if any(col in fieldnames for col in allowed):
        return "multi"
    raise ValueError(
        "Header row required with either 'full_address' or one or more of: "
        "address_line1, address_line2, city, region, postal_code, country"
    )


def normalize_file(
    input_csv_path: str, output_csv_path: str, config_path: str
) -> Tuple[int, str]:
    cfg = config_loader.load_config(config_path)

    with open(input_csv_path, "r", encoding="utf-8", newline="") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError("Header row is required (CSV has no field names).")
        schema_mode = detect_schema(reader.fieldnames)

        rows_out = []
        for row in reader:
            addr_raw = build_input_address_raw(
                row,
                default_country_if_us_zip=cfg.defaults.country_if_us_zip,
                mode=schema_mode,
            )
            non_physical = bool(NON_PHYSICAL_RE.search(addr_raw))
            input_id = compute_input_id(addr_raw)
            rows_out.append(
                {
                    "input_id": input_id,
                    "input_address_raw": addr_raw,
                    "non_physical_flag": "true" if non_physical else "false",
                }
            )

    # Ensure output directory exists
    Path(os.path.dirname(output_csv_path) or ".").mkdir(parents=True, exist_ok=True)

    # Deterministic write in the same order as input
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(
            f_out,
            fieldnames=["input_id", "input_address_raw", "non_physical_flag"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for r in rows_out:
            writer.writerow(r)

    return (len(rows_out), schema_mode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize address CSV (Sprint 1).")
    parser.add_argument("--input", required=True, help="Path to input CSV.")
    parser.add_argument(
        "--output", required=True, help="Path to write data/normalized.csv."
    )
    parser.add_argument("--config", required=True, help="Path to YAML config file.")
    args = parser.parse_args()

    count, mode = normalize_file(args.input, args.output, args.config)
    print(f"Normalized {count} rows using schema mode: {mode}")


if __name__ == "__main__":
    main()
