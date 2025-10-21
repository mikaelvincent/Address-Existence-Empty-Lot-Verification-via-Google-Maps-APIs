"""YAML configuration loader with environment-based secret resolution.

Notes (Sprint 1):
- Secrets are NOT stored in the YAML file; only the ENV VAR names are.
- We validate cache TTL constraints (<= 30 days) to respect Google Maps Platform terms.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict

import yaml


@dataclass(frozen=True)
class APIConfig:
    google_maps_api_key_env: str
    address_validation_api_key_env: str
    url_signing_secret_env: str | None = None

    def get_google_maps_api_key(self) -> str | None:
        return os.getenv(self.google_maps_api_key_env)

    def get_address_validation_api_key(self) -> str | None:
        return os.getenv(self.address_validation_api_key_env)

    def get_url_signing_secret(self) -> str | None:
        return (
            os.getenv(self.url_signing_secret_env)
            if self.url_signing_secret_env
            else None
        )


@dataclass(frozen=True)
class Thresholds:
    stale_years: int
    footprint_radius_m: int


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_seconds: float
    jitter_seconds: float


@dataclass(frozen=True)
class Concurrency:
    workers: int


@dataclass(frozen=True)
class Defaults:
    country_if_us_zip: str


@dataclass(frozen=True)
class CachePolicy:
    latlng_ttl_days: int


@dataclass(frozen=True)
class Compliance:
    no_scraping: bool
    use_official_apis_only: bool
    use_street_view_metadata_only_in_automation: bool
    maps_urls_ok_for_human_review: bool


@dataclass(frozen=True)
class Config:
    project_name: str
    project_version: str
    api: APIConfig
    thresholds: Thresholds
    retry: RetryPolicy
    concurrency: Concurrency
    defaults: Defaults
    cache_policy: CachePolicy
    compliance: Compliance

    def validate(self) -> None:
        # Enforce TTL <= 30 days as per Google Maps Platform service-specific terms.
        if self.cache_policy.latlng_ttl_days > 30:
            raise ValueError(
                f"cache_policy.latlng_ttl_days={self.cache_policy.latlng_ttl_days} "
                "exceeds 30-day maximum for latitude/longitude caching."
            )
        if self.thresholds.stale_years <= 0:
            raise ValueError("thresholds.stale_years must be positive.")
        if self.thresholds.footprint_radius_m <= 0:
            raise ValueError("thresholds.footprint_radius_m must be positive.")
        if self.retry.max_attempts < 1:
            raise ValueError("retry.max_attempts must be >= 1.")
        if self.concurrency.workers < 1:
            raise ValueError("concurrency.workers must be >= 1.")


def _require_key(d: Dict[str, Any], key: str) -> Any:
    if key not in d:
        raise KeyError(f"Missing required configuration key: {key}")
    return d[key]


def load_config(path: str) -> Config:
    """Load and validate YAML configuration from `path`.

    Environment variables are not required in Sprint 1 but are resolved via helper methods.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    project = raw.get("project", {})
    api_raw = raw.get("api", {})
    thresholds_raw = raw.get("thresholds", {})
    retry_raw = raw.get("retry", {})
    concurrency_raw = raw.get("concurrency", {})
    defaults_raw = raw.get("defaults", {})
    cache_raw = raw.get("cache_policy", {})
    compliance_raw = raw.get("compliance", {})

    cfg = Config(
        project_name=_require_key(project, "name"),
        project_version=_require_key(project, "version"),
        api=APIConfig(
            google_maps_api_key_env=_require_key(api_raw, "google_maps_api_key_env"),
            address_validation_api_key_env=_require_key(
                api_raw, "address_validation_api_key_env"
            ),
            url_signing_secret_env=api_raw.get("url_signing_secret_env"),
        ),
        thresholds=Thresholds(
            stale_years=int(_require_key(thresholds_raw, "stale_years")),
            footprint_radius_m=int(_require_key(thresholds_raw, "footprint_radius_m")),
        ),
        retry=RetryPolicy(
            max_attempts=int(_require_key(retry_raw, "max_attempts")),
            base_seconds=float(_require_key(retry_raw, "base_seconds")),
            jitter_seconds=float(_require_key(retry_raw, "jitter_seconds")),
        ),
        concurrency=Concurrency(workers=int(_require_key(concurrency_raw, "workers"))),
        defaults=Defaults(
            country_if_us_zip=_require_key(defaults_raw, "country_if_us_zip")
        ),
        cache_policy=CachePolicy(
            latlng_ttl_days=int(_require_key(cache_raw, "latlng_ttl_days"))
        ),
        compliance=Compliance(
            no_scraping=bool(_require_key(compliance_raw, "no_scraping")),
            use_official_apis_only=bool(
                _require_key(compliance_raw, "use_official_apis_only")
            ),
            use_street_view_metadata_only_in_automation=bool(
                _require_key(
                    compliance_raw, "use_street_view_metadata_only_in_automation"
                )
            ),
            maps_urls_ok_for_human_review=bool(
                _require_key(compliance_raw, "maps_urls_ok_for_human_review")
            ),
        ),
    )

    cfg.validate()
    return cfg
