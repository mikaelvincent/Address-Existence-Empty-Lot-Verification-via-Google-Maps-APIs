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
  * `GOOGLE_ADDRESS_VALIDATION_API_KEY` (only needed when using Address Validation)
  * `GOOGLE_URL_SIGNING_SECRET` (optional; recommended by Google for some signed calls)

**Policy notes**

* Cache **only** latitude/longitude (TTL ≤ **30 days**) and cacheable **Google IDs** (e.g., Place IDs, pano IDs).
* Do **not** scrape google.com/maps or bulk export content outside permitted API responses.

### 3) Run (current pipeline)

**Normalize → Geocode → Street View metadata**

Using Python:

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
```

Or via `make`:

```bash
make normalize IN=data/your_input.csv OUT=data/normalized.csv
make geocode   IN=data/normalized.csv OUT=data/geocode.csv LOG=data/logs/geocode_api_log.jsonl
make svmeta    IN=data/geocode.csv    OUT=data/streetview_meta.csv LOG=data/logs/streetview_meta_api_log.jsonl
```

**Determinism tip:** To make `sv_stale_flag` reproducible across reruns, set an anchor date:

```bash
export SV_ANCHOR_DATE_UTC=2025-01-01   # YYYY-MM-DD (UTC)
```

---

## What you get (current modules)

* **`data/normalized.csv`**
  `input_id` (stable SHA‑256 of `v1|<input_address_raw>`), `input_address_raw`, `non_physical_flag`.

* **`data/geocode.csv`**
  `input_id`, `input_address_raw`, `geocode_status`, `lat`, `lng`, `location_type`, `api_error_codes`.
  📄 Logs: `data/logs/geocode_api_log.jsonl`.
  🗃️ Cache: `data/cache/geocode_cache.sqlite` storing **lat/lng only** (TTL ≤ 30 days).

* **`data/streetview_meta.csv`**
  `input_id`, `sv_metadata_status`, `sv_image_date` (`YYYY-MM` or `YYYY`), `sv_stale_flag`.
  📄 Logs: `data/logs/streetview_meta_api_log.jsonl`.

For the full target output schema and decision logic, see the **Enhanced CSV** and rule set in the [Development Spec](devspec/dev_spec_and_plan.md) §5–§7.

---

## Configuration highlights

* **`thresholds.stale_years`** — flags Street View imagery as stale (default 7 years).
* **`thresholds.footprint_radius_m`** — radius for future building‑footprint proximity checks.
* **`cache_policy.latlng_ttl_days`** — must be ≤ 30 (enforced).
* **`concurrency.workers`** — thread pool size for API calls.
* **`retry`** — exponential backoff parameters.

---

## Compliance (essentials)

* ✅ Use **official** Google Maps Platform APIs only.
* ✅ For automation, query **Street View metadata** only; **no** bulk image downloads.
* ✅ Provide **Google Maps URLs** for human review (no API key required to open).
* ✅ Cache **only** lat/lng (≤ 30 days) and cacheable **Google IDs**.
* ❌ Do **not** scrape google.com/maps or export content beyond licensed API fields.

See the full [Compliance Checklist](docs/compliance_checklist.md).

---

## Tests

```bash
pytest -q
```

Unit tests cover ingestion determinism & PO Box detection, geocoding behavior (OK/zero‑results/retry), and Street View metadata parsing/staleness.
