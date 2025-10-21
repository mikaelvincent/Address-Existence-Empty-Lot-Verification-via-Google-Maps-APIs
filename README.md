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
* Set environment variables for your keys:

  * `GOOGLE_MAPS_API_KEY`
  * `GOOGLE_ADDRESS_VALIDATION_API_KEY`
  * `GOOGLE_URL_SIGNING_SECRET` (optional; recommended by Google for some signed calls)

**Policy notes**

* Cache **only** latitude/longitude (TTL ≤ **30 days**) and cacheable **Google IDs** (e.g., Place IDs, pano IDs).
* Do **not** scrape google.com/maps or bulk export content outside permitted API responses.

### 3) Run (current pipeline)

**Normalize → Geocode → Street View metadata → Footprints proximity → Conditional Address Validation → Decision & URLs**

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
```

Or via `make`:

```bash
make normalize IN=data/your_input.csv OUT=data/normalized.csv
make geocode   IN=data/normalized.csv OUT=data/geocode.csv LOG=data/logs/geocode_api_log.jsonl
make svmeta    IN=data/geocode.csv    OUT=data/streetview_meta.csv LOG=data/logs/streetview_meta_api_log.jsonl
make footprints IN=data/geocode.csv FP="data/footprints/*.geojson" OUT=data/footprints.csv LOG=data/logs/footprints_log.jsonl
make validate  GEOCODE=data/geocode.csv SVMETA=data/streetview_meta.csv FP=data/footprints.csv NORM=data/normalized.csv OUT=data/validation.csv LOG=data/logs/address_validation_api_log.jsonl
make decide    GEOCODE=data/geocode.csv SVMETA=data/streetview_meta.csv FP=data/footprints.csv VALID=data/validation.csv NORM=data/normalized.csv OUT=data/enhanced.csv QA=data/logs/decision_summary.json
```

**Determinism tip:** Upstream modules write no timestamps to CSV. `enhanced.csv` includes a `run_timestamp_utc`. For reproducible tests, set:

```bash
export RUN_ANCHOR_TIMESTAMP_UTC="2025-01-01T00:00:00+00:00"
```

---

## Compliance (essentials)

* ✅ Use **official** Google Maps Platform APIs only.
* ✅ For automation, query **Street View metadata** only; **no** bulk image downloads.
* ✅ Provide **Google Maps URLs** for human review (no API key required to open).
* ✅ Cache **only** lat/lng (≤ 30 days) and cacheable **Google IDs**.
* ❌ Do **not** scrape google.com/maps or export content beyond licensed API fields.

> The decision engine generates Maps URLs only (no scraping) and follows §7.6 of the spec for labels and reason codes. P.O. Boxes are always labeled `NON_PHYSICAL_ADDRESS` (spec §12).
