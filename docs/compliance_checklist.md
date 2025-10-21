# Compliance Checklist

Guardrails that apply across the entire project.

## Scope & Data Use
- ✅ Use **only** official Google Maps Platform APIs (Geocoding, Street View **metadata**, Address Validation).
- ✅ **Do not** scrape google.com/maps or bulk‑export Google Maps content outside permitted API responses.
- ✅ Street View **metadata** is permitted for automation; **do not** bulk download Street View images.
- ✅ Provide Google Maps **URLs** for human reviewers (no API key required to open).
- ✅ Cache only values expressly allowed by policy:
  - Latitude/longitude (TTL **≤ 30 days**, then delete).
  - Google IDs explicitly permitted (e.g., Place IDs, pano IDs) when used.
- ✅ Do **not** cache other Google Maps Platform response content.

## Secrets & Security
- ✅ API keys are pulled from **environment variables**; keys are not stored in source control.
- ✅ Recommend restricting keys by IP/app and enabling usage quotas in Google Cloud.

## Determinism & Auditability
- ✅ Given identical inputs + config, modules produce deterministic outputs.
- ✅ `input_id` is a stable SHA‑256 of a canonicalized address representation.
- ✅ Write structured logs (PII‑safe) for API attempts and decisions.

## Human Review Path
- ✅ Deliverables embed Google Maps URLs for 1‑click spot checks by reviewers.

## Implementation Notes (current modules)
- ✅ CSV ingestion supports **single‑line** (`full_address`) or **multi‑field** schemas.
- ✅ Rule‑based detection for non‑physical addresses (e.g., P.O. Box/CMRA patterns).
- ✅ If `country` is missing but `postal_code` is a US ZIP, default country to **United States**.
- ✅ Geocoding integrates retries/backoff and a TTL‑bounded lat/lng cache only.
- ✅ Street View integration uses the **metadata** endpoint; **no** image downloads in automation.

> These checks are referenced by CI and code review. Any code that violates these constraints should be rejected.
