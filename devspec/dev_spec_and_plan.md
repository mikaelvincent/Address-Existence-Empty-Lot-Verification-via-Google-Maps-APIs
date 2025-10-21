# Specification: Address Existence & Empty‑Lot Verification via Google Maps APIs

## 1) Purpose and scope

This specification defines the single source of truth for building, running, and delivering a system that:

1. Processes a list of 1,000 mailing addresses.
2. Confirms that each address corresponds to a valid, existing physical location.
3. Flags records that are incorrect or appear to be empty lots, and routes edge cases to human review.

The system **must** use official Google Maps Platform APIs and **must not** scrape the Google Maps website or bulk download Google Maps content (compliance requirement) ([Maps ToS](https://cloud.google.com/maps-platform/terms)).
Street View **image metadata** may be queried programmatically and is available **at no charge** without consuming image quota; Street View **images** themselves are **not required** for automation and are only used via human click‑through URLs (no bulk download) ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
Google Maps URLs will be embedded in outputs for one‑click, manual spot checks and do **not** require an API key ([Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)).

---

## 2) Success criteria

* **Coverage:** Produce a decision label for **100%** of input rows.
* **Compliance:** No scraping or bulk content export; only official APIs, plus Maps URLs in deliverables ([Maps ToS](https://cloud.google.com/maps-platform/terms); [Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)).
* **Determinism:** All rules and thresholds below yield reproducible results on re‑run with the same inputs and API versions.
* **Auditability:** Each output row includes evidence fields (signals, timestamps, reasons) enabling end‑to‑end traceability.
* **Human efficiency:** Fewer than **25%** of total rows require manual review under the baseline rules.
* **Delivery:** One enhanced CSV and one human‑review kit (CSV + rubric) as described in §9 and §10.

---

## 3) Key concepts and signals

* **Geocode precision (`location_type`):**

  * `ROOFTOP` → precise building‑level geocode.
  * `RANGE_INTERPOLATED` / `GEOMETRIC_CENTER` / `APPROXIMATE` → lower precision approximations ([Geocoding location_type](https://developers.google.com/maps/documentation/javascript/geocoding)).
* **Street View metadata:** Status and capture date near the coordinate; **no quota consumed** for metadata. The `date` field may be year‑month or year‑only, or omitted ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
* **Building‑footprint proximity:** Whether an open building polygon exists within a small radius of the geocode (Microsoft Global ML Building Footprints dataset) ([Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints)).
* **Postal address validation (selective):** Standardization + deliverability verdicts, executed **only** on ambiguous cases ([Address Validation—overview](https://developers.google.com/maps/documentation/address-validation/overview)).
* **Google Maps URL:** A clickable link for human review; no API key required ([Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)).

---

## 4) Inputs

* **Primary data:** CSV file with 1,000 rows of addresses.

  * **Accepted schemas:**

    * **Single‑line**: `full_address` (e.g., “1600 Amphitheatre Pkwy, Mountain View, CA 94043, USA”).
    * **Multi‑field** (any subset): `address_line1`, `address_line2`, `city`, `region` (state/province), `postal_code`, `country`.
  * **Encoding:** UTF‑8.
  * **Header row:** Required.
* **Configuration (YAML):**

  * Google API keys and endpoint toggles.
  * Rule thresholds (e.g., proximity radius, stale imagery threshold).
  * Country normalization defaults (if `country` missing).
  * Concurrency and backoff settings.
* **Optional auxiliary data:** Local copies of building‑footprint tiles, indexed for spatial queries ([Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints)).

---

## 5) Outputs

### 5.1 Enhanced CSV (authoritative deliverable)

One row per input address with the following **columns (exact names, types)**:

| Column                   | Type         | Description                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------ | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `input_id`               | string       | Stable row identifier from ingestion.                                                                                                                                                                                                                                                                                                                                                                         |
| `input_address_raw`      | string       | Original unmodified address string used for geocoding.                                                                                                                                                                                                                                                                                                                                                        |
| `std_address`            | string       | Standardized address (if Address Validation ran; else empty).                                                                                                                                                                                                                                                                                                                                                 |
| `geocode_status`         | string       | `OK`, `ZERO_RESULTS`, or error code from Geocoding API.                                                                                                                                                                                                                                                                                                                                                       |
| `lat`                    | decimal(9,6) | Latitude (WGS84) if geocoded.                                                                                                                                                                                                                                                                                                                                                                                 |
| `lng`                    | decimal(9,6) | Longitude (WGS84) if geocoded.                                                                                                                                                                                                                                                                                                                                                                                |
| `location_type`          | enum         | `ROOFTOP`, `RANGE_INTERPOLATED`, `GEOMETRIC_CENTER`, `APPROXIMATE` ([Geocoding location_type](https://developers.google.com/maps/documentation/javascript/geocoding)).                                                                                                                                                                                                                                        |
| `sv_metadata_status`     | string       | Status string exactly as returned by the Street View Image Metadata API: one of OK, ZERO_RESULTS, NOT_FOUND, OVER_QUERY_LIMIT, REQUEST_DENIED, INVALID_REQUEST, or UNKNOWN_ERROR. ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata))                                                                                                                                       |
| `sv_image_date`          | string       | `YYYY-MM` or `YYYY` when present; empty if unavailable ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).                                                                                                                                                                                                                                                                 |
| `sv_stale_flag`          | boolean      | `true` if image date older than configured threshold (default: 7 years).                                                                                                                                                                                                                                                                                                                                      |
| `footprint_within_m`     | integer      | Distance (meters) to nearest footprint centroid; `-1` if none found.                                                                                                                                                                                                                                                                                                                                          |
| `footprint_present_flag` | boolean      | `true` if a footprint polygon lies within `footprint_radius_m` (default: 20 m).                                                                                                                                                                                                                                                                                                                               |
| `validation_ran_flag`    | boolean      | `true` if Address Validation executed for this row.                                                                                                                                                                                                                                                                                                                                                           |
| `validation_verdict`     | enum         | **Derived** simplification of the API’s `verdict` object (e.g., set `VALID` when `addressComplete=true` and `hasUnconfirmedComponents=false` at `validationGranularity` ≥ `PREMISE`; else `UNCONFIRMED`/`INVALID` per rule table; `NOT_RUN` when validation was skipped) ([Address Validation—Understand response](https://developers.google.com/maps/documentation/address-validation/understand-response)). |
| `non_physical_flag`      | boolean      | `true` if the input appears to be a P.O. Box/CMRA detected by regex/rules.                                                                                                                                                                                                                                                                                                                                    |
| `google_maps_url`        | string       | One‑click link for manual verification ([Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)).                                                                                                                                                                                                                                                                                      |
| `final_flag`             | enum         | `VALID_LOCATION`, `INVALID_ADDRESS`, `LIKELY_EMPTY_LOT`, `NEEDS_HUMAN_REVIEW`, `NON_PHYSICAL_ADDRESS`.                                                                                                                                                                                                                                                                                                        |
| `reason_codes`           | string[]     | Pipe‑delimited machine‑readable reasons (see §7.4).                                                                                                                                                                                                                                                                                                                                                           |
| `notes`                  | string       | Optional short note for reviewers (e.g., “SV date 2015”).                                                                                                                                                                                                                                                                                                                                                     |
| `run_timestamp_utc`      | datetime     | ISO‑8601 timestamp of decision.                                                                                                                                                                                                                                                                                                                                                                               |
| `api_error_codes`        | string[]     | Pipe‑delimited list if any API calls errored and were retried/finally failed.                                                                                                                                                                                                                                                                                                                                 |

### 5.2 Human‑review kit

* **CSV:** Filtered subset where `final_flag ∈ {LIKELY_EMPTY_LOT, NEEDS_HUMAN_REVIEW}` with `google_maps_url`, `input_address_raw`, and compact evidence columns.
* **Reviewer rubric (PDF/MD):** Deterministic guidance and screenshots of good/edge cases.
* **Review log template (CSV):** Columns: `input_id`, `review_decision` (`CONFIRM_VALID`, `CONFIRM_EMPTY_LOT`, `CONFIRM_INVALID`, `UNSURE`), `reviewer_initials`, `review_notes`.

---

## 6) Compliance requirements

* **No scraping or bulk export** of Google Maps content; do not harvest geocodes, imagery, or business data outside API responses and allowed caches ([Maps ToS](https://cloud.google.com/maps-platform/terms)).
* **Street View metadata only** for automation; loading images programmatically at scale is out of scope. Metadata is free and quota‑free; **metadata requests require an API key and may require a digital signature**; only image loads are billed ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
* **Use Maps URLs** for manual checks; embedding Maps URLs requires no API key and is permitted for cross‑platform opening ([Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)).
* **Billing and quotas** follow Google’s current pricing; do not hard‑code prices—read from the public pricing page during planning ([Core pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)).
* **Caching limit:** Any cached latitude/longitude (including geocoded results) obtained from Google Maps Platform may be cached for **up to 30 consecutive days only**, after which it **must be deleted**; enforce TTL ≤ 30 days on all such cached values ([Service‑Specific Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms)).

---

## 7) Processing pipeline (deterministic, idempotent)

### 7.1 Ingestion & normalization

1. Load CSV.
2. Generate `input_id` as a stable hash of concatenated address fields (SHA‑256 hex).
3. Build `input_address_raw` as:

   * If single‑line present → use as‑is.
   * Else join non‑empty parts: `address_line1`, `address_line2`, `city`, `region`, `postal_code`, `country`.
4. Normalize whitespace; preserve casing and punctuation for geocoding.
5. Detect **non‑physical** addresses using regex rules: `(?i)\b(P\.?O\.?\s*BOX|POST OFFICE BOX|LOCKBOX|PMB|PRIVATE MAILBOX|SUITE\s*#?\s*[\dA-Z]+ AT UPS STORE)\b` → set `non_physical_flag=true`.
6. If `country` missing, default to “United States” **only if** `postal_code` matches US ZIP regex; otherwise leave blank and rely on geocoder biasing parameters (see §8.1).

### 7.2 Geocoding (all rows)

* Call Geocoding API to obtain `lat`, `lng`, `geocode_status`, `location_type`, and formatted address for reference ([Geocoding requests](https://developers.google.com/maps/documentation/geocoding/requests-geocoding); [Geocoding location_type](https://developers.google.com/maps/documentation/javascript/geocoding)).
* Error handling: retry 429/5xx with exponential backoff; cap at 3 retries.
* If `geocode_status=ZERO_RESULTS` → skip downstream steps and set `final_flag=INVALID_ADDRESS` with reasons.

### 7.3 Street View image metadata (all rows with coordinates)

* Call Street View Static API **metadata** endpoint with the coordinate; persist `sv_metadata_status` and `sv_image_date` ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
* **Authentication:** Include an API **key**; a **digital signature** is recommended and **required in certain instances**. Metadata requests are **no‑charge and do not consume quota**; only image loads are billed. ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata))
* Compute `sv_stale_flag = true` if `sv_image_date` is older than `stale_years` (default 7), or if the date is missing but `sv_metadata_status=OK`.
* **Clarifier:** When you pass `location` as lat/lng, the Street View system resolves to a panorama with **~50 m accuracy**; this search behavior **is not configurable** on the metadata endpoint. (The separate **imagery** request supports a `radius` parameter; the **metadata** endpoint does not.) ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata); [SV request](https://developers.google.com/maps/documentation/streetview/request-streetview))

### 7.4 Building‑footprint proximity (all rows with coordinates)

* Spatially query for the **nearest footprint centroid** within `footprint_radius_m` (default 20 m).
* Set `footprint_present_flag = true` if a polygon is found within the radius; set `footprint_within_m` to nearest‑centroid distance; otherwise `-1` ([Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints)).
* Implementation note in §8.3 covers dataset tiling and spatial index.

### 7.5 Conditional Address Validation (ambiguous rows only)

Run Address Validation **only** on rows meeting any of these conditions:

* `location_type` ∈ {`RANGE_INTERPOLATED`, `GEOMETRIC_CENTER`, `APPROXIMATE`}; or
* `footprint_present_flag=false`; or
* `sv_metadata_status=ZERO_RESULTS`; or
* `sv_stale_flag=true`; or
* `non_physical_flag=true`.

Capture `std_address` and **derive** a simplified `validation_verdict` from the API’s `verdict` object (e.g., consider `VALID` when `addressComplete=true` and `hasUnconfirmedComponents=false` at `validationGranularity` ≥ `PREMISE`; otherwise map to `UNCONFIRMED`/`INVALID` per the rule table) ([Address Validation—Understand response](https://developers.google.com/maps/documentation/address-validation/understand-response)).

### 7.6 Decision engine (labels + reasons)

Apply rules **in order**:

1. **Hard invalid**

   * If `geocode_status=ZERO_RESULTS` → `final_flag=INVALID_ADDRESS` (`reason_codes=NO_GEOCODE`).
   * Else if `validation_ran_flag=true` and **derived** `validation_verdict=INVALID` → `final_flag=INVALID_ADDRESS` (`reason_codes=POSTAL_INVALID`).

2. **Non‑physical**

   * If `non_physical_flag=true` → `final_flag=NON_PHYSICAL_ADDRESS` (`reason_codes=NON_PHYSICAL`).

3. **Auto‑valid location**

   * If `location_type=ROOFTOP` **AND** (`footprint_present_flag=true` **OR** `sv_metadata_status=OK`) → `final_flag=VALID_LOCATION` (`reason_codes` includes any relevant signals, e.g., `ROOFTOP`, `FOOTPRINT_MATCH`, `SV_OK`).

4. **Likely empty lot**

   * If `location_type≠ROOFTOP` **AND** `footprint_present_flag=false` **AND** (`sv_metadata_status=OK` **OR** `sv_metadata_status=ZERO_RESULTS`) → `final_flag=LIKELY_EMPTY_LOT` (`reason_codes=NO_FOOTPRINT|LOW_PRECISION_GEOCODE` plus `SV_OK` or `SV_ZERO_RESULTS`).

5. **Needs human review**

   * All other cases, or when conflicting signals arise (e.g., `ROOFTOP` but `sv_stale_flag=true` and `footprint_present_flag=false`), set `final_flag=NEEDS_HUMAN_REVIEW` with explicit reason codes.

**Reason codes (controlled vocabulary):**
`NO_GEOCODE`, `POSTAL_INVALID`, `NON_PHYSICAL`, `ROOFTOP`, `LOW_PRECISION_GEOCODE`, `FOOTPRINT_MATCH`, `NO_FOOTPRINT`, `SV_OK`, `SV_ZERO_RESULTS`, `SV_STALE`.

### 7.7 Google Maps URL (all rows)

* Construct a universal Maps URL searching for the standardized or raw address:
  `https://www.google.com/maps/search/?api=1&query=<urlencoded_address>`; include lat/lng variant if present for precision ([Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)).

### 7.8 Idempotency, retries, and caching

* **Idempotent run key:** hash of input CSV bytes + config YAML.
* **HTTP retries:** 429/5xx exponential backoff with jitter, max 3 attempts.
* **Local cache:** Cache **only** fields that Google expressly permits:
  • **Latitude/longitude** values (**TTL ≤ 30 days**, then delete), and
  • **Google IDs** that are explicitly cacheable by policy, including **Place IDs** and **Street View `pano_ID`** (per “Google ID Caching”).
  Do **not** cache other Google Maps Platform response content. Enforce key‑based TTL for lat/lng and treat other fields as non‑cacheable. ([Service‑Specific Terms — Google ID Caching](https://cloud.google.com/maps-platform/terms/maps-service-terms))
* **Concurrency:** configurable worker pool; default 10 concurrent outbound requests with rate guards to respect API quotas ([Core pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)).

---

## 8) External integrations

### 8.1 Geocoding API

* **Endpoint:** Geocoding API (HTTP) with `address` (or `components`) and optional `region` bias ([Geocoding requests](https://developers.google.com/maps/documentation/geocoding/requests-geocoding)).
* **Fields used:** `status`, `results[0].geometry.location`, `results[0].geometry.location_type`.
* **Precision semantics:** as documented for `ROOFTOP`, `RANGE_INTERPOLATED`, `GEOMETRIC_CENTER`, `APPROXIMATE` ([Geocoding location_type](https://developers.google.com/maps/documentation/javascript/geocoding)).

### 8.2 Street View Static API — **metadata only**

* **Endpoint:** `/streetview/metadata` with `location=lat,lng` (or `pano`) and an API key; a digital signature is recommended and may be required. Metadata requests are **no‑charge and do not consume quota**; only image loads are billed ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
* **Clarifier:** When `location` is a lat/lng, Google searches for a panorama within ~50 m automatically; this radius is **not configurable** on the metadata endpoint ([SV request](https://developers.google.com/maps/documentation/streetview/request-streetview)).

### 8.3 Building‑footprint dataset

* **Source:** Microsoft Global ML Building Footprints (country/state tiles in GeoJSON/Parquet/Shapefile) ([Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints)).
* **Method:**

  1. Build an R‑tree (or use a spatial DB) over polygon centroids;
  2. For each coordinate, query nearest centroid within `footprint_radius_m`;
  3. If found, set `footprint_present_flag=true` and compute `footprint_within_m`.
* **Notes:** Dataset quality varies by region; treat proximity as a **signal** rather than ground truth (source README) ([Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints)).

### 8.4 Address Validation API (conditional)

* **Endpoint:** Address Validation API `validateAddress` with `address` parcelized from raw input or geocoded components.
* **Fields used:** the `verdict` object (e.g., `addressComplete`, `validationGranularity`, `geocodeGranularity`, `hasUnconfirmedComponents`, and optionally `possibleNextAction`); **the system derives a simplified verdict** used elsewhere in this spec (see §5.1 and §7.5) ([Address Validation—Understand response](https://developers.google.com/maps/documentation/address-validation/understand-response); [Address Validation—validateAddress](https://developers.google.com/maps/documentation/address-validation/reference/rest/v1/TopLevel/validateAddress)).

### 8.5 Google Maps URLs

* **Construction:** `https://www.google.com/maps/search/?api=1&query=<encoded>`; optionally `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=<lat>,<lng>` for Street View deep‑links **for human reviewers** ([Maps URLs—Get started](https://developers.google.com/maps/documentation/urls/get-started)).

---

## 9) Reviewer rubric (deterministic guidance)

Reviewers see the human‑review CSV and open `google_maps_url`. They will:

1. **Check pin & parcel:** If the pin or address corresponds to a clear structure within the parcel bounds, mark `CONFIRM_VALID`.
2. **Check emptiness:** If satellite/street views show clear, unbuilt land or parking with no principal structure, mark `CONFIRM_EMPTY_LOT`.
3. **Check errors:** If the pin is misplaced far from the intended road segment or address labels are inconsistent/obviously wrong, mark `CONFIRM_INVALID`.
4. **Uncertain/stale imagery:** If Street View is missing or imagery is obviously outdated, mark `UNSURE` and add a note; the system treats `UNSURE` as `NEEDS_HUMAN_REVIEW`.

**Important:** Street View imagery may be months or years old; reviewers must consider the **capture date** shown by Google in the UI or provided via metadata before making a “lot is empty” call ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata); [How Street View works](https://www.google.com/streetview/how-it-works/)).

---

## 10) Delivery artifacts

* **Enhanced CSV** with all fields in §5.1.
* **Human‑review kit** (CSV subset + rubric + review log template).
* **Run report (PDF/MD):** Counts by `final_flag`, reasons distribution, API error rates, and notes on any addresses skipped due to persistent API errors (should be zero).
* **Operational log bundle:** JSONL of per‑row API calls and decisions for audit.

---

## 11) Non‑functional requirements

* **Performance:** End‑to‑end throughput of ≥200 addresses/minute on a typical workstation with API concurrency of 10, subject to network and quotas ([Core pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)).
* **Security:** API keys stored in environment variables; restrict keys by IP/app; no keys in source control ([Getting started with Maps Platform](https://developers.google.com/maps/get-started)).
* **Privacy:** Input addresses retained locally; no PII or addresses transmitted to third parties beyond the listed APIs. Logs redact API keys and tokens.
* **Observability:** Structured logs at INFO (progress) and DEBUG (request/response samples with PII‑safe redaction).
* **Reproducibility:** Same inputs + config + cache must reproduce identical outputs.

---

## 12) Error handling and edge cases

* **API errors:** Retry 429/5xx with exponential backoff; after 3 failures mark `api_error_codes` and set `final_flag=NEEDS_HUMAN_REVIEW` with reason `API_FAILURE`.
* **Ambiguous geocodes:** If multiple candidates tie, select the first by Google ranking and mark `reason_codes+=AMBIGUOUS`.
* **International addresses:** Prefer componentized requests (`components=country:XX`) when `country` is specified; otherwise allow global search.
* **Coordinates off‑road:** If `ROOFTOP` yet `footprint_present_flag=false`, route to review (possible new build or dataset gap).
* **P.O. Boxes:** Always labeled `NON_PHYSICAL_ADDRESS` regardless of validation verdict.

---

## 13) Cost and quotas

* **Cost control:**

  * **Cache only fields that Google expressly permits:**
    • **Latitude/longitude** values (**TTL ≤ 30 days**, then delete), and
    • **Google IDs** that are explicitly cacheable by policy, including **Place IDs** and **Street View `pano_ID`** (per “Google ID Caching”).
    Do **not** cache other Google Maps Platform response content. ([Service‑Specific Terms — Google ID Caching](https://cloud.google.com/maps-platform/terms/maps-service-terms))
  * Run Address Validation **only** for ambiguous rows per §7.5.
* **Quotas and pricing:** Consult Google’s public pricing table and adjust concurrency to stay within configured quotas ([Core pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)).
* **No assumptions** in code about free allowances; read from configuration.

---

## 14) Data model examples

**Geocoding sample (trimmed):** `status=OK`, `geometry.location={lat,lng}`, `geometry.location_type="ROOFTOP"` ([Geocoding requests](https://developers.google.com/maps/documentation/geocoding/requests-geocoding); [Geocoding location_type](https://developers.google.com/maps/documentation/javascript/geocoding)).
**Street View metadata sample:** `status=OK`, `date="2020-07"`; may be year‑only or omitted ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
**Address Validation sample:** `result.verdict.addressComplete=true/false`, `result.verdict.validationGranularity`, `result.verdict.hasUnconfirmedComponents`, `result.address.formattedAddress` ([Address Validation—Understand response](https://developers.google.com/maps/documentation/address-validation/understand-response)).

---

## 15) Repository structure (reference)

```
/devspec/             (this document, rubric)
/config/              (YAML: keys, thresholds, concurrency)
/data/                (inputs and outputs)
/src/
  ingest.py
  geocode.py
  streetview_meta.py
  footprints.py
  validate_postal.py
  decide.py
  urls.py
  review_pack.py
  reporting.py
/tests/
  unit/
  integration/
```

---

## 16) Acceptance tests (minimum)

1. **Determinism:** Re‑running with identical inputs produces byte‑identical enhanced CSV.
2. **Invalids:** A known bad address yields `INVALID_ADDRESS` with `NO_GEOCODE` reason.
3. **Non‑physical:** A known P.O. Box yields `NON_PHYSICAL_ADDRESS`.
4. **Auto‑valid:** A well‑known, established building yields `VALID_LOCATION` with `ROOFTOP` or `FOOTPRINT_MATCH`.
5. **Ambiguity routing:** An interpolated geocode with no footprint yields `LIKELY_EMPTY_LOT` or `NEEDS_HUMAN_REVIEW` per §7.6.
6. **Compliance:** No code path fetches Street View **images** for automation; only metadata and URLs are generated ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata); [Maps ToS](https://cloud.google.com/maps-platform/terms)).

---

## 17) Risks and mitigations

* **Stale imagery → false “empty lot.”** Use `sv_image_date` and `sv_stale_flag`; route to review when stale ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
* **Footprint gaps/quality.** Treat as a signal only; combine with geocode precision; route conflicts to review ([Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints)).
* **Quota exhaustion.** Concurrency throttles + caching; monitor error rates; optionally pause between batches ([Core pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)).
* **Non‑US formats.** Rely on componentized queries and validation where available ([Address Validation—overview](https://developers.google.com/maps/documentation/address-validation/overview)).
* **Compliance drift.** Pin API usage to documented endpoints and avoid scraping or storing content beyond license terms ([Maps ToS](https://cloud.google.com/maps-platform/terms); [Service‑Specific Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms)).

---

# Development Timeline (Sprint Plan)

> **Execution model:** Each sprint is a self‑contained mini‑project with clear inputs and outputs, designed to be executed independently (e.g., by separate GPT‑5 Pro instances). No sprint assumes unstated context beyond artifacts listed as inputs.

---

## **Sprint 1 — Project bootstrap & compliance guardrails**

**Objective:** Establish configuration, secrets, and compliance boundaries; parse and normalize the input CSV.
**Inputs:** Raw CSV, initial config template.
**Tasks:**

* Implement config loader (YAML): API keys, thresholds, concurrency.
* Secrets handling (env vars); verify key restrictions ([Getting started with Maps Platform](https://developers.google.com/maps/get-started)).
* CSV ingestion with schema detection and `input_id` hashing.
* Non‑physical detection rules.
  **Deliverables:**
* `data/normalized.csv` (columns: `input_id`, `input_address_raw`, non‑physical flag).
* `config/config.yml` (filled).
* Compliance checklist (1‑page) referencing ToS and API scope ([Maps ToS](https://cloud.google.com/maps-platform/terms)).
  **Acceptance criteria:** Passes unit tests for ingestion; compliance checklist approved.

---

## **Sprint 2 — Geocoding integration and baseline signals**

**Objective:** Geocode all rows and persist precision signals.
**Inputs:** `data/normalized.csv`, `config/config.yml`.
**Tasks:**

* Implement Geocoding requests with retries/backoff.
* Parse `status`, `lat`, `lng`, `location_type`.
* Write cache layer with TTL ≤ 30 days for geocode results ([Service‑Specific Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms)).
  **Deliverables:**
* `data/geocode.csv` (joinable by `input_id` with fields in §5.1).
* API call log (JSONL).
  **Acceptance criteria:** 100% rows attempted; 0% unhandled errors; deterministic rerun ([Geocoding requests](https://developers.google.com/maps/documentation/geocoding/requests-geocoding); [Geocoding location_type](https://developers.google.com/maps/documentation/javascript/geocoding)).

---

## **Sprint 3 — Street View metadata integration**

**Objective:** Attach Street View availability and capture date to each geocoded row.
**Inputs:** `data/geocode.csv`.
**Tasks:**

* Call `/streetview/metadata` for each coordinate; include API key (signature recommended); compute `sv_stale_flag`. Metadata requests are **no‑charge and do not consume quota**; only image loads are billed ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).
* Document that the ~50 m search radius is automatic and **not configurable** on the metadata endpoint when passing coordinates ([SV request](https://developers.google.com/maps/documentation/streetview/request-streetview)).
  **Deliverables:**
* `data/streetview_meta.csv` (fields in §5.1).
  **Acceptance criteria:** 100% rows with coordinates enriched; no image requests; deterministic rerun.

---

## **Sprint 4 — Building‑footprint proximity module**

**Objective:** Determine whether a building polygon exists near each coordinate.
**Inputs:** `data/geocode.csv`, footprint tiles.
**Tasks:**

* Ingest country/state tiles; build spatial index.
* Compute nearest‑centroid distance and presence flag per §7.4.
  **Deliverables:**
* `data/footprints.csv` (fields: `input_id`, `footprint_within_m`, `footprint_present_flag`).
  **Acceptance criteria:** Spatial queries complete within performance budget; spot‑checks documented against sample locations ([Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints)).

---

## **Sprint 5 — Conditional Address Validation**

**Objective:** Validate ambiguous addresses and capture standardized mailing forms.
**Inputs:** Joins from Sprints 2–4.
**Tasks:**

* Implement `validateAddress` for rows matching §7.5 conditions.
* Persist `std_address` and **derive** `validation_verdict` from the API’s `verdict` object as defined in §5.1 ([Address Validation—Understand response](https://developers.google.com/maps/documentation/address-validation/understand-response); [Address Validation—validateAddress](https://developers.google.com/maps/documentation/address-validation/reference/rest/v1/TopLevel/validateAddress)).
  **Deliverables:**
* `data/validation.csv` (fields in §5.1).
  **Acceptance criteria:** Only targeted rows are validated; logs show zero unexpected API errors; costs observable.

---

## **Sprint 6 — Decision engine & URL generation**

**Objective:** Assign final labels deterministically and generate Maps URLs.
**Inputs:** Joins from Sprints 2–5; `data/normalized.csv`.
**Tasks:**

* Implement rule order and reason codes (§7.6).
* Generate Maps URLs for each row ([Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)).
  **Deliverables:**
* `data/enhanced.csv` (full schema in §5.1).
  **Acceptance criteria:** 100% rows labeled; reproducible; automated QA summary with counts per `final_flag`.

---

## **Sprint 7 — Human‑review kit**

**Objective:** Package the subset requiring review with a clear rubric.
**Inputs:** `data/enhanced.csv`.
**Tasks:**

* Filter to `final_flag ∈ {LIKELY_EMPTY_LOT, NEEDS_HUMAN_REVIEW}`.
* Produce reviewer rubric and review log template.
  **Deliverables:**
* `data/review_queue.csv`, `docs/reviewer_rubric.pdf`, `data/review_log_template.csv`.
  **Acceptance criteria:** Random sample of 30 rows validated by internal test reviewers; rubric clarifies decisions referencing metadata dates ([SV metadata](https://developers.google.com/maps/documentation/streetview/metadata)).

---

## **Sprint 8 — Consolidation, QA, and final package**

**Objective:** Merge any completed human reviews (if provided), finalize deliverables, and produce a run report.
**Inputs:** `data/enhanced.csv`, optional `data/review_log_completed.csv`.
**Tasks:**

* Apply reviewer outcomes to override labels where applicable.
* Generate final CSV and summary report with metrics, errors, and method notes.
  **Deliverables:**
* **Final enhanced CSV** (authoritative).
* **Run report** (PDF/MD) with counts, reason distributions, and any unresolved edge cases.
* **Operational logs** (JSONL bundle).
  **Acceptance criteria:** All artifacts present; QA checklist signed; compliance statements included ([Maps ToS](https://cloud.google.com/maps-platform/terms); [Core pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)).

---

## 18) Handover checklist

* Config file with thresholds and API keys (keys not checked into repo).
* Exact versions of dependencies and OS.
* Link to Google billing dashboard and quotas used ([Core pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)).
* Copies or references of footprint tiles used and any indexing scripts.
* This specification, reviewer rubric, and the run report.
