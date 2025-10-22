"""Consolidation, QA, and final packaging.

Functions:
- Apply reviewer outcomes (if provided) to override final labels
- Generate the final enhanced CSV (authoritative)
- Produce a run report (Markdown + optional PDF via fpdf2)
- Emit a compact JSONL with final decisions for audit

Inputs:
- data/enhanced.csv
- Optional: data/review_log_completed.csv

Outputs:
- data/final_enhanced.csv (same schema as enhanced.csv; labels overridden where applicable)
- docs/run_report.md (summary metrics, counts, distributions, notes, run key if available)
- docs/run_report.pdf (if fpdf2 is installed)
- data/logs/final_decisions.jsonl (per-row final decision snapshot)

Compliance:
- No API calls here. We only read/write local CSV/JSONL/MD/PDF artifacts.
- We do not modify or generate Google content; Maps URLs in CSV are carried forward only.

Determinism:
- If environment var RUN_ANCHOR_TIMESTAMP_UTC is set to an ISO‑8601 timestamp,
  it is used in the report header; otherwise current UTC time is used.

Idempotency:
- If present, we read a run key from `data/logs/decision_summary.json` (written by the
  decision step) and include it in the report header to correlate artifacts across runs.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config_loader  # type: ignore


# ------------------------------
# Utilities
# ------------------------------

def _ensure_dir(p: str) -> None:
    Path(os.path.dirname(p) or ".").mkdir(parents=True, exist_ok=True)


def _read_csv_as_list(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV missing header: {path}")
        return list(reader.fieldnames), list(reader)


def _write_csv_with_headers(path: str, headers: List[str], rows: List[Dict[str, str]]) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _anchor_timestamp() -> str:
    """ISO-8601 UTC timestamp, optionally anchored by env for reproducibility."""
    env = os.getenv("RUN_ANCHOR_TIMESTAMP_UTC")
    if env:
        s = env.strip().replace("Z", "+00:00")
        try:
            dt.datetime.fromisoformat(s)
            return s
        except Exception:
            pass
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _maybe_read_run_key(default_summary_path: str = "data/logs/decision_summary.json") -> str:
    """Return run key if present in the decision summary JSON; else empty string."""
    try:
        with open(default_summary_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        rk = str(obj.get("run_key") or "").strip()
        return rk
    except Exception:
        return ""


# ------------------------------
# Human review merge
# ------------------------------

@dataclass(frozen=True)
class ReviewDecision:
    decision: str          # CONFIRM_VALID | CONFIRM_EMPTY_LOT | CONFIRM_INVALID | UNSURE
    reviewer_initials: str
    review_notes: str


# Mapping from reviewer decision to final_flag override
_DECISION_TO_FLAG = {
    "CONFIRM_VALID": "VALID_LOCATION",
    "CONFIRM_EMPTY_LOT": "LIKELY_EMPTY_LOT",
    "CONFIRM_INVALID": "INVALID_ADDRESS",
    "UNSURE": "NEEDS_HUMAN_REVIEW",
}


def _load_reviews(path: Optional[str]) -> Dict[str, ReviewDecision]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    _, rows = _read_csv_as_list(path)
    out: Dict[str, ReviewDecision] = {}
    for r in rows:
        iid = (r.get("input_id") or "").strip()
        dec = (r.get("review_decision") or "").strip().upper()
        ini = (r.get("reviewer_initials") or "").strip()
        notes = (r.get("review_notes") or "").strip()
        if not iid or not dec:
            continue
        if dec not in _DECISION_TO_FLAG:
            continue
        out[iid] = ReviewDecision(decision=dec, reviewer_initials=ini, review_notes=notes)
    return out


@dataclass(frozen=True)
class OverrideInfo:
    input_id: str
    old_flag: str
    new_flag: str
    decision: str
    reviewer_initials: str


def _apply_overrides(
    headers: List[str],
    rows: List[Dict[str, str]],
    reviews: Dict[str, ReviewDecision],
) -> Tuple[List[Dict[str, str]], List[OverrideInfo]]:
    """Return (updated_rows, overrides_info)."""
    updated: List[Dict[str, str]] = []
    overrides: List[OverrideInfo] = []

    # Ensure required columns exist — we don't add new columns to keep schema stable
    needed = {"input_id", "final_flag", "notes"}
    missing = [c for c in needed if c not in headers]
    if missing:
        raise ValueError(f"enhanced.csv missing required columns: {missing}")

    for r in rows:
        iid = r.get("input_id", "")
        existing_flag = (r.get("final_flag") or "").strip()
        note = r.get("notes", "").strip()
        rv = reviews.get(iid)
        if rv:
            new_flag = _DECISION_TO_FLAG.get(rv.decision, existing_flag)
            if new_flag != existing_flag:
                overrides.append(
                    OverrideInfo(
                        input_id=iid,
                        old_flag=existing_flag,
                        new_flag=new_flag,
                        decision=rv.decision,
                        reviewer_initials=rv.reviewer_initials,
                    )
                )
            # Always annotate notes with human action (even if same flag)
            annotation = f"Human override: {rv.decision}"
            if rv.reviewer_initials:
                annotation += f" ({rv.reviewer_initials})"
            if rv.review_notes:
                annotation += f" — {rv.review_notes}"
            r["final_flag"] = new_flag
            r["notes"] = f"{note} | {annotation}".strip(" |") if note else annotation
        updated.append(r)
    return updated, overrides


# ------------------------------
# Metrics & report
# ------------------------------

@dataclass(frozen=True)
class RunMetrics:
    total_rows: int
    counts_by_flag: Dict[str, int]
    reason_counts: Dict[str, int]
    rows_with_any_api_error: int
    api_error_counts: Dict[str, int]
    sv_stale_true: int
    non_physical_true: int
    unresolved_count: int                    # LIKELY_EMPTY_LOT + NEEDS_HUMAN_REVIEW
    unresolved_examples: List[Dict[str, str]]
    overrides_applied: int


def _aggregate_metrics(rows: List[Dict[str, str]], overrides: List[OverrideInfo]) -> RunMetrics:
    total = len(rows)
    by_flag = collections.Counter((r.get("final_flag") or "").strip() for r in rows)

    # Reason codes distribution (pipe-delimited)
    reason_cnt: collections.Counter[str] = collections.Counter()
    for r in rows:
        rc = (r.get("reason_codes") or "").strip()
        if not rc:
            continue
        for token in rc.split("|"):
            token = token.strip()
            if token:
                reason_cnt[token] += 1

    # API errors
    api_err_cnt: collections.Counter[str] = collections.Counter()
    rows_with_err = 0
    for r in rows:
        errs = (r.get("api_error_codes") or "").strip()
        if errs:
            rows_with_err += 1
            for e in errs.split("|"):
                e = e.strip()
                if e:
                    api_err_cnt[e] += 1

    sv_stale_true = sum(1 for r in rows if (r.get("sv_stale_flag") or "").strip().lower() == "true")
    non_physical_true = sum(1 for r in rows if (r.get("non_physical_flag") or "").strip().lower() == "true")

    unresolved_flags = {"LIKELY_EMPTY_LOT", "NEEDS_HUMAN_REVIEW"}
    unresolved_rows = [r for r in rows if (r.get("final_flag") or "") in unresolved_flags]
    unresolved_count = len(unresolved_rows)
    # Include up to 10 examples with compact info for the report
    examples = []
    for r in unresolved_rows[:10]:
        examples.append(
            {
                "input_id": r.get("input_id", ""),
                "final_flag": r.get("final_flag", ""),
                "input_address_raw": r.get("input_address_raw", ""),
                "google_maps_url": r.get("google_maps_url", ""),
                "reason_codes": r.get("reason_codes", ""),
                "notes": r.get("notes", ""),
            }
        )

    return RunMetrics(
        total_rows=total,
        counts_by_flag=dict(sorted(by_flag.items(), key=lambda kv: kv[0])),
        reason_counts=dict(sorted(reason_cnt.items(), key=lambda kv: (-kv[1], kv[0]))),
        rows_with_any_api_error=rows_with_err,
        api_error_counts=dict(sorted(api_err_cnt.items(), key=lambda kv: (-kv[1], kv[0]))),
        sv_stale_true=sv_stale_true,
        non_physical_true=non_physical_true,
        unresolved_count=unresolved_count,
        unresolved_examples=examples,
        overrides_applied=len(overrides),
    )


def _latin1_sanitize(text: str) -> str:
    replacements = {
        "\u2014": " - ",
        "\u2013": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2022": "*",
        "\u2018": "'",
        "\u2019": "'",
        "\u201C": '"',
        "\u201D": '"',
        "\u2026": "...",
        "\u200B": "",
        "\u2060": "",
        "\u00A0": " ",
    }
    out = text
    for k, v in replacements.items():
        out = out.replace(k, v)
    out = "".join(ch if ord(ch) <= 255 else "?" for ch in out)
    return out


def _write_text_pdf(text: str, out_path: str) -> Tuple[bool, str]:
    """Write a simple text PDF using fpdf2 if available.

    Rendering hardening for broad viewer compatibility:
    - Force left alignment (avoid justification quirks in some renderers).
    - Use w=0 to span to the right margin safely.
    - Force black text color explicitly.
    - Prefer modern cursor controls (new_x/new_y); fall back if unavailable.
    """
    try:
        from fpdf import FPDF  # type: ignore
        try:
            from fpdf.enums import WrapMode  # type: ignore
        except Exception:
            WrapMode = None  # type: ignore
    except Exception:
        return (False, "fpdf2 not installed; skipping PDF (Markdown was written).")

    _ensure_dir(out_path)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    # Make text color explicit and conservative
    try:
        pdf.set_text_color(0, 0, 0)
        pdf.set_draw_color(0, 0, 0)
    except Exception:
        pass

    for line in text.splitlines():
        # Render headings as plain lines (strip leading '#')
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        sanitized = _latin1_sanitize(line)

        # Build kwargs compatibly across fpdf2 versions
        common_kwargs = {"align": "L"}  # avoid justification
        if WrapMode:
            common_kwargs["wrapmode"] = WrapMode.CHAR

        try:
            # Preferred in modern fpdf2: explicit cursor movement after each line
            pdf.multi_cell(0, 6, txt=sanitized, new_x="LMARGIN", new_y="NEXT", **common_kwargs)  # type: ignore[arg-type]
        except TypeError:
            # Older versions: fall back to basic call
            pdf.multi_cell(0, 6, txt=sanitized, **common_kwargs)

    try:
        pdf.output(out_path)
        return (True, "PDF created.")
    except Exception as e:
        return (False, f"Failed to write PDF: {e}")


def _format_counts_table(d: Dict[str, int]) -> str:
    if not d:
        return "_(none)_\n"
    lines = ["| Label | Count |", "| --- | ---: |"]
    for k, v in d.items():
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines) + "\n"


def _format_kv_table(d: Dict[str, int], key_hdr: str, val_hdr: str) -> str:
    if not d:
        return "_(none)_\n"
    lines = [f"| {key_hdr} | {val_hdr} |", "| --- | ---: |"]
    for k, v in d.items():
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines) + "\n"


def _build_report_md(metrics: RunMetrics, run_ts: str, cfg: config_loader.Config, run_key: str) -> str:
    unresolved_flags = {"LIKELY_EMPTY_LOT", "NEEDS_HUMAN_REVIEW"}
    rk_line = f"\n**Run key:** `{run_key}`\n" if run_key else ""
    return f"""# Run Report — Address Existence & Empty‑Lot Verification

**Run timestamp (UTC):** {run_ts}{rk_line}

## Summary
- **Total rows:** {metrics.total_rows}
- **Overrides applied from human review:** {metrics.overrides_applied}
- **Rows with any API errors recorded:** {metrics.rows_with_any_api_error}
- **Street View stale (flag==true):** {metrics.sv_stale_true}
- **Non‑physical addresses detected:** {metrics.non_physical_true}
- **Unresolved after consolidation** (`{", ".join(sorted(unresolved_flags))}`): {metrics.unresolved_count}

## Final counts by label
{_format_counts_table(metrics.counts_by_flag)}

## Reason codes — frequency
{_format_kv_table(metrics.reason_counts, "Reason code", "Rows")}

## API errors — frequency
{_format_kv_table(metrics.api_error_counts, "API error code", "Occurrences")}

## Unresolved examples (up to 10)
{_format_unresolved_examples(metrics.unresolved_examples)}

---

## Method notes
- Final labels are produced by the deterministic ingestion → decision engine pipeline, with **optional human‑review overrides**.
- This report is generated by consolidation tooling; **no new API calls** are made here.
- Caching & compliance: only **lat/lng** (TTL ≤ {cfg.cache_policy.latlng_ttl_days} days) and permitted **Google IDs** are cached; no scraping or bulk export of Google content.
"""


def _format_unresolved_examples(rows: List[Dict[str, str]]) -> str:
    if not rows:
        return "_(none)_\n"
    lines = []
    for r in rows:
        addr = r.get("input_address_raw", "")
        url = r.get("google_maps_url", "")
        rc = r.get("reason_codes", "")
        notes = r.get("notes", "")
        iid = r.get("input_id", "")
        lines.append(f"- **{iid}** — {addr}\n  - Flag: `{r.get('final_flag','')}`\n  - Reasons: `{rc}`\n  - URL: {url}\n  - Notes: {notes}")
    return "\n".join(lines) + "\n"


# ------------------------------
# JSONL (final decisions)
# ------------------------------

def _write_final_jsonl(
    out_path: str,
    rows: List[Dict[str, str]],
    overrides: List[OverrideInfo],
) -> None:
    _ensure_dir(out_path)
    overrides_by_id = {o.input_id: o for o in overrides}
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            iid = r.get("input_id", "")
            ov = overrides_by_id.get(iid)
            rec = {
                "input_id": iid,
                "final_flag": r.get("final_flag", ""),
                "source": "HUMAN" if ov else "AUTO",
                "review_decision": ov.decision if ov else "",
                "reviewer_initials": ov.reviewer_initials if ov else "",
                "reason_codes": (r.get("reason_codes") or ""),
                "google_maps_url": (r.get("google_maps_url") or ""),
                "api_error_codes": (r.get("api_error_codes") or "").split("|") if (r.get("api_error_codes") or "") else [],
                # Compact evidence snapshot
                "location_type": r.get("location_type", ""),
                "footprint_present_flag": r.get("footprint_present_flag", ""),
                "footprint_within_m": r.get("footprint_within_m", ""),
                "sv_metadata_status": r.get("sv_metadata_status", ""),
                "sv_image_date": r.get("sv_image_date", ""),
                "sv_stale_flag": r.get("sv_stale_flag", ""),
                "non_physical_flag": r.get("non_physical_flag", ""),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ------------------------------
# Orchestration (CLI)
# ------------------------------

def run_reporting(
    enhanced_csv_path: str,
    final_csv_out: str,
    report_md_out: str,
    report_pdf_out: str,
    decisions_jsonl_out: str,
    config_path: str,
    review_log_completed_path: Optional[str] = None,
) -> int:
    """Merge human reviews (if any), write final CSV + report + JSONL.

    Returns number of processed rows.
    """
    # Load config (for policy info / anchor only)
    cfg = config_loader.load_config(config_path)

    # Read enhanced
    headers, rows = _read_csv_as_list(enhanced_csv_path)

    # Load reviews & apply overrides
    reviews = _load_reviews(review_log_completed_path)
    updated_rows, overrides = _apply_overrides(headers, rows, reviews)

    # Deterministic write: same headers/order as input
    _write_csv_with_headers(final_csv_out, headers, updated_rows)

    # Metrics & report
    metrics = _aggregate_metrics(updated_rows, overrides)
    run_ts = _anchor_timestamp()
    run_key = _maybe_read_run_key()  # best-effort; may be empty
    report_md = _build_report_md(metrics, run_ts, cfg, run_key)
    _ensure_dir(report_md_out)
    with open(report_md_out, "w", encoding="utf-8") as f:
        f.write(report_md)
    # Optional PDF
    _write_text_pdf(report_md, report_pdf_out)

    # Final decisions JSONL
    _write_final_jsonl(decisions_jsonl_out, updated_rows, overrides)

    return len(updated_rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Consolidate results and produce the run report.")
    p.add_argument("--enhanced", required=True, help="Path to data/enhanced.csv")
    p.add_argument(
        "--reviews",
        required=False,
        default=None,
        help="Optional path to data/review_log_completed.csv.",
    )
    p.add_argument(
        "--final-out",
        required=False,
        default="data/final_enhanced.csv",
        help="Path to write the authoritative final enhanced CSV (default: data/final_enhanced.csv)",
    )
    p.add_argument(
        "--report-md",
        required=False,
        default="docs/run_report.md",
        help="Path to write the Markdown run report (default: docs/run_report.md)",
    )
    p.add_argument(
        "--report-pdf",
        required=False,
        default="docs/run_report.pdf",
        help="Path to write the PDF run report (default: docs/run_report.pdf)",
    )
    p.add_argument(
        "--log-jsonl",
        required=False,
        default="data/logs/final_decisions.jsonl",
        help="Path to write final decisions JSONL (default: data/logs/final_decisions.jsonl)",
    )
    p.add_argument("--config", required=True, help="Path to config/config.yml")
    args = p.parse_args()

    n = run_reporting(
        enhanced_csv_path=args.enhanced,
        final_csv_out=args.final_out,
        report_md_out=args.report_md,
        report_pdf_out=args.report_pdf,
        decisions_jsonl_out=args.log_jsonl,
        config_path=args.config,
        review_log_completed_path=args.reviews,
    )
    print(f"Consolidated {n} rows -> {args.final_out}")


if __name__ == "__main__":
    main()
