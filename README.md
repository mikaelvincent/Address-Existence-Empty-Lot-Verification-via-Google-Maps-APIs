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

**Normalize → Geocode → Street View metadata → Footprints proximity**

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

# Footprint proximity to data/footprints.csv
# `--footprints` accepts one or more files or globs:
#   - GeoJSON FeatureCollection
#   - NDJSON with one Feature per line
#   - CSV with headers: lat,lng
python src/footprints.py \
  --geocode data/geocode.csv \
  --footprints data/footprints/*.geojson \
  --output data/footprints.csv \
  --config config/config.yml \
  --log data/logs/footprints_log.jsonl
```

Or via `make`:

```bash
make normalize IN=data/your_input.csv OUT=data/normalized.csv
make geocode   IN=data/normalized.csv OUT=data/geocode.csv LOG=data/logs/geocode_api_log.jsonl
make svmeta    IN=data/geocode.csv    OUT=data/streetview_meta.csv LOG=data/logs/streetview_meta_api_log.jsonl
make footprints IN=data/geocode.csv FP="data/footprints/*.geojson" OUT=data/footprints.csv LOG=data/logs/footprints_log.jsonl
```

**Determinism tip:** To make `sv_stale_flag` reproducible across reruns, set an anchor date:

```bash
export SV_ANCHOR_DATE_UTC=2025-01-01   # YYYY-MM-DD (UTC)
```

---

## Configuration highlights

* **`thresholds.stale_years`** — flags Street View imagery as stale (default 7 years).
* **`thresholds.footprint_radius_m`** — **presence radius** for building‑footprint proximity (default 20 m).
* **`cache_policy.latlng_ttl_days`** — must be ≤ 30 (enforced).
* **`concurrency.workers`** — thread pool size for API calls/modules.
* **`retry`** — exponential backoff parameters.

---

## Footprints module notes

* Input footprints can be downloaded from the [Microsoft Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints) repository (GeoJSON or CSV).
* This module indexes **centroids** only (not polygons) using a fixed‑degree grid, then computes nearest‑neighbor distances via the **haversine** formula.
* For very large regions, consider pre‑filtering tiles to your countries/states of interest or generating a centroids CSV to reduce memory.

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

Unit tests cover ingestion determinism & PO Box detection, geocoding behavior (OK/zero‑results/retry), Street View metadata parsing/staleness, and building‑footprint proximity.
