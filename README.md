# Address Existence & Empty‑Lot Verification (Google Maps APIs)

Verify that mailing addresses correspond to real, physical locations and flag likely empty lots—using only **official Google Maps Platform APIs** and policy‑compliant caching.

## What this project does

- **Normalize input addresses** (single‑line or multi‑field CSV).
- **Geocode** each address and capture precision (`location_type`).
- Attach **Street View metadata** (availability + capture date) — *no image downloads for automation*.
- Check **building‑footprint proximity** using Microsoft’s Global ML Building Footprints.
- Run **Address Validation** on ambiguous cases only.
- Apply a **deterministic decision engine** to label each row and generate **Google Maps URLs** for 1‑click human checks.
- Produce a **human‑review kit** and a **final run report**.

## Features & APIs

- **Geocoding API** — coordinates + precision.
- **Street View metadata** — availability & date (metadata only; no quota/billing for metadata).
- **Address Validation API** — standardized form & deliverability signals (conditional).
- **Maps URLs** — safe links for reviewers; no API key required to open.

## Requirements

- Python 3.9+ (recommended)
- Google Maps Platform API keys (provided via environment variables)

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Configure

Set the required secrets as environment variables (recommended):

```bash
export GOOGLE_MAPS_API_KEY="your_key_here"
export GOOGLE_ADDRESS_VALIDATION_API_KEY="your_key_here"
# optional (for certain signed calls)
export GOOGLE_URL_SIGNING_SECRET="your_secret_here"
```

Or use a local `.env`:

```bash
cp .env.example .env
# Edit .env, then load for this shell:
export $(grep -v '^#' .env | xargs)
```

Configuration file: [`config/config.yml`](config/config.yml)
This YAML stores **names of env vars**, thresholds, and concurrency—**not** secrets.

### Policy notes (summary)

* Use **only** official Google Maps Platform APIs.
* Cache **only** latitude/longitude (TTL ≤ **30 days**) and permitted **Google IDs** (e.g., Place IDs, pano IDs).
* Do **not** scrape google.com/maps or bulk‑export content beyond permitted API responses.

## Run the pipeline

**Normalize → Geocode → Street View metadata → Footprints proximity → Conditional Address Validation → Decision & URLs → Human‑review kit → Final consolidation & report**

```bash
# Normalize input CSV to data/normalized.csv
python src/normalize_addresses.py \
  --input data/your_input.csv \
  --output data/normalized.csv \
  --config config/config.yml

# Geocode to data/geocode.csv (logs to data/logs/)
python src/geocode.py \
  --normalized data/normalized.csv \
  --output data/geocode.csv \
  --config config/config.yml \
  --log data/logs/geocode_api_log.jsonl

# Street View metadata to data/streetview_meta.csv
python src/streetview_meta.py \
  --geocode data/geocode.csv \
  --output data/streetview_meta.csv \
  --config config/config.yml \
  --log data/logs/streetview_meta_api_log.jsonl

# Footprint proximity to data/footprints.csv
python src/footprints.py \
  --geocode data/geocode.csv \
  --footprints data/footprints/*.geojson \
  --output data/footprints.csv \
  --config config/config.yml \
  --log data/logs/footprints_log.jsonl

# Conditional Address Validation to data/validation.csv
python src/validate_postal.py \
  --geocode data/geocode.csv \
  --svmeta data/streetview_meta.csv \
  --footprints data/footprints.csv \
  --normalized data/normalized.csv \
  --output data/validation.csv \
  --config config/config.yml \
  --log data/logs/address_validation_api_log.jsonl

# Decision engine & Maps URLs to data/enhanced.csv (+ summary JSON)
python src/decide.py \
  --geocode data/geocode.csv \
  --svmeta data/streetview_meta.csv \
  --footprints data/footprints.csv \
  --validation data/validation.csv \
  --normalized data/normalized.csv \
  --output data/enhanced.csv \
  --config config/config.yml \
  --summary data/logs/decision_summary.json

# Human‑review kit
python src/review_pack.py \
  --enhanced data/enhanced.csv \
  --queue-out data/review_queue.csv \
  --log-template-out data/review_log_template.csv \
  --rubric-out-md docs/reviewer_rubric.md \
  --rubric-out-pdf docs/reviewer_rubric.pdf \
  --config config/config.yml

# Final consolidation & run report
python src/reporting.py \
  --enhanced data/enhanced.csv \
  --reviews data/review_log_completed.csv \
  --final-out data/final_enhanced.csv \
  --report-md docs/run_report.md \
  --report-pdf docs/run_report.pdf \
  --log-jsonl data/logs/final_decisions.jsonl \
  --config config/config.yml
```

Or via `make`:

```bash
make normalize IN=data/your_input.csv OUT=data/normalized.csv
make geocode   IN=data/normalized.csv OUT=data/geocode.csv LOG=data/logs/geocode_api_log.jsonl
make svmeta    IN=data/geocode.csv    OUT=data/streetview_meta.csv LOG=data/logs/streetview_meta_api_log.jsonl
make footprints IN=data/geocode.csv FP="data/footprints/*.geojson" OUT=data/footprints.csv LOG=data/logs/footprints_log.jsonl
make validate  GEOCODE=data/geocode.csv SVMETA=data/streetview_meta.csv FP=data/footprints.csv NORM=data/normalized.csv OUT=data/validation.csv LOG=data/logs/address_validation_api_log.jsonl
make decide    GEOCODE=data/geocode.csv SVMETA=data/streetview_meta.csv FP=data/footprints.csv VALID=data/validation.csv NORM=data/normalized.csv OUT=data/enhanced.csv QA=data/logs/decision_summary.json
make review    IN=data/enhanced.csv QOUT=data/review_queue.csv LTOUT=data/review_log_template.csv RMD=docs/reviewer_rubric.md RPDF=docs/reviewer_rubric.pdf
make report    ENH=data/enhanced.csv REV=data/review_log_completed.csv FINAL=data/final_enhanced.csv MD=docs/run_report.md PDF=docs/run_report.pdf JLOG=data/logs/final_decisions.jsonl
```

### Reproducibility

For reproducible timestamps in outputs:

```bash
export RUN_ANCHOR_TIMESTAMP_UTC="2025-01-01T00:00:00+00:00"
```

> The run report PDF is generated if `fpdf2` is installed; Markdown is always written.
