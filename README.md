# Address Existence & Empty‑Lot Verification (Google Maps APIs)

Verify that mailing addresses correspond to real, physical locations and flag potential empty lots—using only **official Google Maps Platform APIs** and policy‑compliant caching.

> **APIs used**
> - Geocoding API (precision + coordinates)
> - Street View **metadata** endpoint (availability + capture date) — **no image downloads for automation**
> - Address Validation API (optional; run on ambiguous cases only)

---

## Quick Start

### 1) Install
```bash
python3 -m pip install -r requirements.txt
```

### 2) Configure

* Edit [`config/config.yml`](config/config.yml) (keeps **names** of environment variables, not secrets).

* **Provide secrets via environment variables** — recommended:

  * `GOOGLE_MAPS_API_KEY`
  * `GOOGLE_ADDRESS_VALIDATION_API_KEY`
  * `GOOGLE_URL_SIGNING_SECRET` (optional; recommended for certain signed URL calls)

#### Option A — export in your shell

```bash
export GOOGLE_MAPS_API_KEY="your_key_here"
export GOOGLE_ADDRESS_VALIDATION_API_KEY="your_key_here"
# optional
export GOOGLE_URL_SIGNING_SECRET="your_secret_here"
```

#### Option B — use a local `.env` file (convenient for dev)

1. Create it from the example:

   ```bash
   cp .env.example .env
   # open .env and paste your values
   ```
2. Load it for the current shell session:

   ```bash
   # bash/zsh
   export $(grep -v '^#' .env | xargs)
   ```

   > If you prefer automation, tools like **direnv** or **python-dotenv** can auto‑load `.env`. No code changes are required because the app already reads from environment variables.

**Policy notes**

* Cache **only** latitude/longitude (TTL ≤ **30 days**) and cacheable **Google IDs** (e.g., Place IDs, pano IDs).
* Do **not** scrape google.com/maps or bulk export content outside permitted API responses.

### 3) Run (current pipeline)

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

# Street View metadata to data/streetview_meta.csv (logs to data/logs/)
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

# Conditional Address Validation to data/validation.csv (logs to data/logs/)
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

# Human‑review kit (Sprint 7)
# Produces: data/review_queue.csv, data/review_log_template.csv, docs/reviewer_rubric.md, docs/reviewer_rubric.pdf*
python src/review_pack.py \
  --enhanced data/enhanced.csv \
  --queue-out data/review_queue.csv \
  --log-template-out data/review_log_template.csv \
  --rubric-out-md docs/reviewer_rubric.md \
  --rubric-out-pdf docs/reviewer_rubric.pdf \
  --config config/config.yml

# Final consolidation & run report (Sprint 8)
# - Merges optional data/review_log_completed.csv
# - Writes data/final_enhanced.csv, docs/run_report.md/.pdf, data/logs/final_decisions.jsonl
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

**Determinism tip:** Upstream modules write no timestamps to intermediate CSVs; `enhanced.csv` includes a run timestamp. For reproducible reports, set:

```bash
export RUN_ANCHOR_TIMESTAMP_UTC="2025-01-01T00:00:00+00:00"
```

**PDF note:** The run report PDF is generated if `fpdf2` is installed (already listed in `requirements.txt`). Otherwise, the Markdown report is always written.
