"""Maps URL builders (policy-compliant, human review only).

This module constructs Google Maps URLs for human reviewers.
It does NOT call any Google APIs and requires no API keys.

References:
- Maps URLs: https://developers.google.com/maps/documentation/urls/get-started
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus


def build_maps_search_url(
    address_fallback: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> str:
    """Return a universal Google Maps search URL.

    Preference:
      1) If lat/lng are provided, use them for precision.
      2) Otherwise, fall back to the (standardized or raw) address string.

    The resulting URL requires no API key and is safe to embed in outputs.
    """
    if lat is not None and lng is not None:
        q = f"{lat:.6f},{lng:.6f}"
    else:
        q = address_fallback.strip()
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(q)}"
