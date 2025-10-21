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
