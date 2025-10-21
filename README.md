# Address Existence & Empty‑Lot Verification — Sprint 1

This sprint bootstraps the project, enforces compliance guardrails, and delivers **CSV ingestion & normalization**.

## What this delivers
- `data/normalized.csv` with columns:
  - `input_id` — stable SHA‑256 hex of `v1|<input_address_raw>`
  - `input_address_raw` — normalized address string (preserves casing/punctuation)
  - `non_physical_flag` — `true` if regex detects P.O. Box/CMRA patterns
- `config/config.yml` — filled with thresholds, retry, concurrency, and ENV‑based secret references
- `docs/compliance_checklist.md` — guardrails aligned with Google Maps Platform terms
- Unit tests covering ingestion, non‑physical detection, default country rule, and determinism

## Install
```bash
python3 -m pip install -r requirements.txt
```

## Run normalization

```bash
python src/normalize_addresses.py \
  --input data/your_input.csv \
  --output data/normalized.csv \
  --config config/config.yml
```

## Test

```bash
pytest -q
```

## Notes

* Header row is **required**.
* Schema detection:

  * **Single‑line**: file has `full_address` column.
  * **Multi‑field**: any of `address_line1,address_line2,city,region,postal_code,country`.
* If `country` is missing and `postal_code` matches a US ZIP, the country defaults to **United States**.
* Secrets are read from environment variables referenced in the config file; none are required for Sprint 1.

---

# Sprint 2 — Geocoding integration and baseline signals

This sprint integrates the **Google Geocoding API** to enrich each normalized row with geocoding signals.

## What this delivers

* `data/geocode.csv` with columns:

  * `input_id`
  * `input_address_raw`
  * `geocode_status`
  * `lat`
  * `lng`
  * `location_type`
  * `api_error_codes`
* API call log: `data/logs/geocode_api_log.jsonl` (PII‑safe)
* **Cache** (SQLite) saving **only** lat/lng with TTL ≤ 30 days: `data/cache/geocode_cache.sqlite`

## Run geocoding

```bash
python src/geocode.py \
  --normalized data/normalized.csv \
  --output data/geocode.csv \
  --config config/config.yml \
  --log data/logs/geocode_api_log.jsonl
```

Or via `make`:

```bash
make geocode IN=data/normalized.csv OUT=data/geocode.csv LOG=data/logs/geocode_api_log.jsonl
```

## Compliance notes

* The cache persists **only** `lat`/`lng` with TTL ≤ 30 days (policy‑compliant).
* No Street View images are fetched; this sprint uses only the Geocoding API.
* Logs avoid storing full API payloads and redact secrets.

---

# Sprint 3 — Street View metadata integration

Attach Street View availability and capture date to each geocoded row using the **Street View Static API — metadata endpoint**.

## What this delivers

* `data/streetview_meta.csv` with columns:

  * `input_id`
  * `sv_metadata_status` (`OK`, `ZERO_RESULTS`, `NOT_FOUND`, etc.)
  * `sv_image_date` (`YYYY-MM` or `YYYY` when present)
  * `sv_stale_flag` (`true` if older than configured `stale_years`, or `true` when status is `OK` but date is missing)

* API call log: `data/logs/streetview_meta_api_log.jsonl` (PII‑safe)

## Run Street View metadata

```bash
python src/streetview_meta.py \
  --geocode data/geocode.csv \
  --output data/streetview_meta.csv \
  --config config/config.yml \
  --log data/logs/streetview_meta_api_log.jsonl
```

Or via `make`:

```bash
make svmeta IN=data/geocode.csv OUT=data/streetview_meta.csv LOG=data/logs/streetview_meta_api_log.jsonl
```

### Determinism tip

To make `sv_stale_flag` reproducible across reruns, set an anchor date:

```bash
export SV_ANCHOR_DATE_UTC=2025-01-01   # YYYY-MM-DD (UTC)
```

### Compliance notes

* This sprint calls the **metadata** endpoint only; **no Street View images** are downloaded programmatically.
* Metadata requests require an API key and are **no-charge**; only image loads are billed.
* Passing `location=<lat,lng>` triggers Google’s automatic panorama search (~50 m) which is **not configurable** on the metadata endpoint.
