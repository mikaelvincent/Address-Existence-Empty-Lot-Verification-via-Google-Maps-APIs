"""Human‑review kit generator (Sprint 7)

- Input:  data/enhanced.csv (authoritative deliverable from Sprint 6)
- Output:
    * data/review_queue.csv
    * data/review_log_template.csv
    * docs/reviewer_rubric.md
    * docs/reviewer_rubric.pdf   (optional; created if fpdf2 is installed)

What goes into the review queue?
- Rows where final_flag ∈ {LIKELY_EMPTY_LOT, NEEDS_HUMAN_REVIEW}

Why these columns?
- Reviewers need a 1‑click Maps URL and compact evidence (precision, footprints,
  Street View status/date/staleness, validation summary) to make a fast call.

Compliance:
- Generates documents and CSVs only. No API calls. No scraping.
- Uses Maps URLs already present in enhanced.csv (safe for human review).
"""

from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

# Local
import config_loader  # type: ignore


TARGET_FLAGS = {"LIKELY_EMPTY_LOT", "NEEDS_HUMAN_REVIEW"}

# Columns we keep for reviewers (compact evidence + URL)
REVIEW_QUEUE_COLUMNS = [
    "input_id",
    "final_flag",
    "input_address_raw",
    "std_address",
    "google_maps_url",
    # Compact evidence
    "location_type",
    "footprint_present_flag",
    "footprint_within_m",
    "sv_metadata_status",
    "sv_image_date",
    "sv_stale_flag",
    "validation_verdict",
    "non_physical_flag",
    "reason_codes",
    "notes",
]


@dataclass(frozen=True)
class ReviewKitPaths:
    enhanced_csv: str
    queue_csv: str
    log_template_csv: str
    rubric_md: str
    rubric_pdf: str


def _read_enhanced(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _ensure_dir(p: str) -> None:
    Path(os.path.dirname(p) or ".").mkdir(parents=True, exist_ok=True)


def _filter_queue(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for r in rows:
        if (r.get("final_flag") or "") in TARGET_FLAGS:
            out.append(r)
    return out


def _write_review_queue(rows: List[Dict[str, str]], out_path: str) -> int:
    _ensure_dir(out_path)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_QUEUE_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in REVIEW_QUEUE_COLUMNS})
    return len(rows)


def _write_review_log_template(rows: List[Dict[str, str]], out_path: str) -> int:
    """Prepopulate one blank review row per queued item."""
    _ensure_dir(out_path)
    headers = ["input_id", "review_decision", "reviewer_initials", "review_notes"]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "input_id": r.get("input_id", ""),
                    "review_decision": "",  # CONFIRM_VALID | CONFIRM_EMPTY_LOT | CONFIRM_INVALID | UNSURE
                    "reviewer_initials": "",
                    "review_notes": "",
                }
            )
    return len(rows)


def _build_rubric_markdown(stale_years: int) -> str:
    """Return rubric text (Markdown).

    Keeps content self‑contained and deterministic.
    """
    return f"""# Reviewer Rubric — Address Existence & Empty‑Lot Verification

This rubric guides human reviewers who open the **Google Maps URL** for each queued row.
Only **maps URLs** are used for review — there are **no automated image downloads**.
Street View **metadata** (status/date) shown in the CSV is for context; imagery may be **stale**.

---

## Decisions you can make
- `CONFIRM_VALID` — A principal structure is clearly present within the parcel / pin area.
- `CONFIRM_EMPTY_LOT` — Land appears unbuilt (or only a minor shed/parking) consistent with an **empty lot**.
- `CONFIRM_INVALID` — The address/pin is clearly wrong (e.g., off by a large distance; wrong road segment).
- `UNSURE` — Evidence is unclear (conflicting signals, low resolution, or imagery is too old).

## What to check (in order)
1. **Pin & parcel alignment:** Does the pin (or address text label) line up with a plausible structure on the parcel?
2. **Structure presence:** Is there a building footprint or obvious structure at street/satellite zoom?
3. **Street View capture date:** If Street View is available, look at the **capture date** (CSV column `sv_image_date`).  
   - If imagery is older than **{stale_years} years**, treat it as **potentially outdated**.
4. **Signals from CSV (compact evidence):**
   - `location_type`: `ROOFTOP` is precise; others are approximations.
   - `footprint_present_flag` and `footprint_within_m`: indicates nearby building footprint.
   - `sv_metadata_status` and `sv_image_date`: Street View availability and date.
   - `validation_verdict`: postal standardization/verdict for ambiguous rows.
   - `reason_codes`: machine‑readable summary of signals (e.g., `NO_FOOTPRINT|LOW_PRECISION_GEOCODE|SV_OK`).

## How to decide
- If **clear structure present** within the pin/parcel → **CONFIRM_VALID**.
- If **no structure** and imagery is **recent** (≲ {stale_years} years) → **CONFIRM_EMPTY_LOT**.
- If pin obviously misplaced (e.g., road segment mismatch or far from intended area) → **CONFIRM_INVALID**.
- If Street View is **missing or stale** and satellite is ambiguous → **UNSURE**.

> Note: Some regions have incomplete building‑footprint datasets; `NO_FOOTPRINT` alone does **not** prove emptiness. Use imagery context.

---

## Review log fields
Fill these in `data/review_log_template.csv`:

- `input_id` — Do not change.
- `review_decision` — One of: `CONFIRM_VALID`, `CONFIRM_EMPTY_LOT`, `CONFIRM_INVALID`, `UNSURE`.
- `reviewer_initials` — Your initials.
- `review_notes` — Optional, short note (e.g., “SV 2015; appears vacant”).

---

## Examples (thought process)
- **Rooftop + recent SV** showing a building → `CONFIRM_VALID`.
- **Approximate geocode + no footprint + recent SV** showing open land → `CONFIRM_EMPTY_LOT`.
- **Rooftop + no footprint + stale SV** (older than {stale_years} years) → **`UNSURE`** unless satellite is clearly conclusive.
- **Pin far off parcel / wrong road** → `CONFIRM_INVALID`.

Be conservative when imagery is old or conflicting. When in doubt, choose `UNSURE`.
"""


def _write_text_pdf(text: str, out_path: str) -> Tuple[bool, str]:
    """Write a simple text PDF using fpdf2 if available.

    Returns (created, note).
    """
    try:
        from fpdf import FPDF  # type: ignore
    except Exception:
        return (False, "fpdf2 not installed; skipping PDF (Markdown was written).")

    _ensure_dir(out_path)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    # Split markdown to lines and render as plain text blocks.
    for line in text.splitlines():
        # Treat headings as bold-ish by prefixing with '• ' for readability
        if line.startswith("#"):
            line = line.lstrip("#").strip()
            if line:
                line = f"{line}"
        pdf.multi_cell(0, 6, txt=line)
    try:
        pdf.output(out_path)
        return (True, "PDF created.")
    except Exception as e:
        return (False, f"Failed to write PDF: {e}")


def run_review_pack(
    enhanced_csv_path: str,
    queue_csv_path: str,
    log_template_csv_path: str,
    rubric_md_path: str,
    rubric_pdf_path: str,
    config_path: str,
) -> int:
    """Generate review kit.

    Returns number of queued rows.
    """
    # Load config for rubric parameters (e.g., stale_years)
    cfg = config_loader.load_config(config_path)

    rows = _read_enhanced(enhanced_csv_path)
    queue_rows = _filter_queue(rows)

    # Deterministic order: preserve original order from enhanced.csv
    n_queue = _write_review_queue(queue_rows, queue_csv_path)
    _write_review_log_template(queue_rows, log_template_csv_path)

    # Rubric (MD + optional PDF)
    rubric_md = _build_rubric_markdown(cfg.thresholds.stale_years)
    _ensure_dir(rubric_md_path)
    with open(rubric_md_path, "w", encoding="utf-8") as f:
        f.write(rubric_md)

    created, note = _write_text_pdf(rubric_md, rubric_pdf_path)
    if not created:
        print(note, flush=True)

    return n_queue


def main() -> None:
    p = argparse.ArgumentParser(description="Generate the human‑review kit (Sprint 7).")
    p.add_argument("--enhanced", required=True, help="Path to data/enhanced.csv")
    p.add_argument(
        "--queue-out",
        required=False,
        default="data/review_queue.csv",
        help="Output CSV for review queue (default: data/review_queue.csv)",
    )
    p.add_argument(
        "--log-template-out",
        required=False,
        default="data/review_log_template.csv",
        help="Output CSV for blank review log (default: data/review_log_template.csv)",
    )
    p.add_argument(
        "--rubric-out-md",
        required=False,
        default="docs/reviewer_rubric.md",
        help="Output Markdown rubric path (default: docs/reviewer_rubric.md)",
    )
    p.add_argument(
        "--rubric-out-pdf",
        required=False,
        default="docs/reviewer_rubric.pdf",
        help="Output PDF rubric path (default: docs/reviewer_rubric.pdf)",
    )
    p.add_argument("--config", required=True, help="Path to config/config.yml")
    args = p.parse_args()

    n = run_review_pack(
        enhanced_csv_path=args.enhanced,
        queue_csv_path=args.queue_out,
        log_template_csv_path=args.log_template_out,
        rubric_md_path=args.rubric_out_md,
        rubric_pdf_path=args.rubric_out_pdf,
        config_path=args.config,
    )
    print(f"Queued {n} rows for human review -> {args.queue_out}")


if __name__ == "__main__":
    main()
