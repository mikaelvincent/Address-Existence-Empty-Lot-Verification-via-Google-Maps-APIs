# Compliance Checklist — Sprint 1

This checklist captures guardrails that govern implementation across sprints.

## Scope & Data Use
- ✅ Use **only** official Google Maps Platform APIs (Geocoding, Street View **metadata**, Address Validation).
- ✅ **Do not** scrape google.com/maps or export Google Maps content outside permitted API responses.
- ✅ Street View **metadata** is permitted for automation; **do not** bulk download Street View images.
- ✅ Provide Google Maps **URLs** for human reviewers (no API key required to open).
- ✅ Cache only values expressly allowed by policy:
  - Latitude/longitude (TTL **≤ 30 days**, then delete).
  - Google IDs explicitly permitted (e.g., Place IDs, pano IDs) when used.
- ✅ Do **not** cache other response content from Google Maps Platform APIs.

## Secrets & Security
- ✅ API keys pulled from **environment variables**; keys are not stored in source control.
- ✅ Recommend restricting keys by IP/app and enabling usage quotas on the Google Cloud project.

## Determinism & Auditability
- ✅ Ingestion produces deterministic outputs given identical inputs + config.
- ✅ `input_id` is a stable SHA‑256 of a canonicalized address representation.

## Human Review Path
- ✅ Deliverables embed Google Maps URLs for 1‑click spot checks by reviewers.

## Implementation Notes (Sprint 1)
- ✅ CSV ingestion supports **single‑line** (`full_address`) or **multi‑field** schemas.
- ✅ Non‑physical address detection is rule/regex‑based (P.O. Box/CMRA patterns).
- ✅ If `country` is missing but `postal_code` matches a US ZIP, default `country` to **United States**.

> This checklist is referenced by CI and code review. Any code that violates these constraints should be rejected.
