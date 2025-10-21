"""Building‑footprint proximity.

- Reads data/geocode.csv (requires: input_id, geocode_status, lat, lng).
- Loads footprint tiles (GeoJSON FeatureCollection, NDJSON Features, or CSV with columns lat,lng).
- Builds a lightweight grid index of centroid points (pure stdlib).
- For each geocoded coordinate, computes nearest‑centroid haversine distance (meters).
- Presence flag is true when the nearest centroid lies within `thresholds.footprint_radius_m`.
- Writes:
    * data/footprints.csv (input_id, footprint_within_m, footprint_present_flag)
    * data/logs/footprints_log.jsonl (optional JSONL log for diagnostics)

Compliance:
- Uses Microsoft Global ML Building Footprints dataset.
- Only centroid points are stored locally; no Google content is cached.

Numerics:
- Centroid computation uses a translated local origin to stabilize the shoelace
  centroid formula for small polygons.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Local config loader
import config_loader  # type: ignore


# ------------------------------
# Utilities
# ------------------------------


def _format_bool(b: bool) -> str:
    return "true" if b else "false"


def _safe_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    R = 6371008.8  # mean Earth radius (m)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(1 - a), math.sqrt(a))
    return R * c


# ------------------------------
# Geo parsing (GeoJSON / CSV)
# ------------------------------


def _ring_area_and_centroid_xy(
    ring: Sequence[Sequence[float]],
) -> Tuple[float, Tuple[float, float]]:
    """Return (signed area in degrees^2, centroid (x,y)=lon,lat) for an outer ring.

    Uses a numerically stable variant of the shoelace centroid:
    - Translate coordinates to a local origin to reduce cancellation.
    - If the polygon is degenerate (|A|≈0), fall back to the bbox center.
    """
    if not ring or len(ring) < 3:
        return 0.0, (float("nan"), float("nan"))

    pts = list(ring)
    if pts[0] != pts[-1]:
        pts.append(pts[0])

    x_ref, y_ref = pts[0]
    A = 0.0
    Cx = 0.0
    Cy = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i][0] - x_ref, pts[i][1] - y_ref
        x1, y1 = pts[i + 1][0] - x_ref, pts[i + 1][1] - y_ref
        cross = x0 * y1 - x1 * y0
        A += cross
        Cx += (x0 + x1) * cross
        Cy += (y0 + y1) * cross

    A *= 0.5
    if abs(A) < 1e-24:
        # Degenerate; use bbox center (original coordinates)
        xs = [p[0] for p in pts[:-1]]
        ys = [p[1] for p in pts[:-1]]
        return 0.0, ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

    cx = Cx / (6.0 * A) + x_ref
    cy = Cy / (6.0 * A) + y_ref
    return A, (cx, cy)


def _feature_centroid_latlng(feature: Dict) -> Optional[Tuple[float, float]]:
    """Compute centroid for GeoJSON Feature with Polygon/MultiPolygon geometry.

    Returns (lat, lng) if available; otherwise None.
    """
    geom = feature.get("geometry") or {}
    gtype = (geom.get("type") or "").upper()
    coords = geom.get("coordinates")
    if not coords or gtype not in {"POLYGON", "MULTIPOLYGON"}:
        return None

    if gtype == "POLYGON":
        area, (x, y) = _ring_area_and_centroid_xy(coords[0])
        if math.isnan(x) or math.isnan(y):
            return None
        return (y, x)

    # MULTIPOLYGON: pick the polygon with the largest absolute outer-ring area
    best_xy: Optional[Tuple[float, float]] = None
    best_area = -1.0
    for poly in coords:
        if not poly or not poly[0]:
            continue
        area, (x, y) = _ring_area_and_centroid_xy(poly[0])
        a = abs(area)
        if math.isnan(x) or math.isnan(y):
            continue
        if a > best_area:
            best_area = a
            best_xy = (x, y)
    if best_xy is None:
        return None
    return (best_xy[1], best_xy[0])  # lat,lng


def load_centroids_from_file(path: str) -> List[Tuple[float, float]]:
    """Load (lat, lng) centroids from a file.

    Supported:
    - GeoJSON FeatureCollection with Polygon/MultiPolygon features
    - NDJSON where each line is a GeoJSON Feature
    - CSV with headers 'lat','lng' (case-insensitive)
    """
    pts: List[Tuple[float, float]] = []
    lower = path.lower()
    if lower.endswith(".csv"):
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            headers = {h.lower(): h for h in (reader.fieldnames or [])}
            lat_key = headers.get("lat")
            lng_key = (
                headers.get("lng") or headers.get("lon") or headers.get("longitude")
            )
            if not lat_key or not lng_key:
                raise ValueError(f"CSV {path} must contain 'lat' and 'lng' headers.")
            for row in reader:
                lat = _safe_float(row.get(lat_key, ""))
                lng = _safe_float(row.get(lng_key, ""))
                if lat is None or lng is None:
                    continue
                pts.append((lat, lng))
        return pts

    # Try to parse as full JSON first
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if (
            isinstance(obj, dict)
            and (obj.get("type") or "").upper() == "FEATURECOLLECTION"
        ):
            features = obj.get("features") or []
            for feat in features:
                c = _feature_centroid_latlng(feat)
                if c:
                    pts.append(c)
            return pts
    except json.JSONDecodeError:
        # Fall back to NDJSON streaming
        pass

    # NDJSON fallback: one Feature per line
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                feat = json.loads(s)
                c = _feature_centroid_latlng(feat)
                if c:
                    pts.append(c)
            except Exception:
                continue
    return pts


# ------------------------------
# Grid index
# ------------------------------


class GridIndex:
    """Simple lat/lng grid index for point lookup.

    Uses fixed cell size in degrees; query inspects the minimal set of cells covering a search radius.
    """

    def __init__(self, cell_deg: float = 0.01) -> None:
        self.cell_deg = float(cell_deg)
        self._cells: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}

    def _key(self, lat: float, lng: float) -> Tuple[int, int]:
        return (
            int(math.floor(lat / self.cell_deg)),
            int(math.floor(lng / self.cell_deg)),
        )

    def add(self, lat: float, lng: float) -> None:
        k = self._key(lat, lng)
        self._cells.setdefault(k, []).append((lat, lng))

    def add_many(self, pts: Iterable[Tuple[float, float]]) -> None:
        for lat, lng in pts:
            self.add(lat, lng)

    def neighbors_within(
        self, lat: float, lng: float, radius_m: float
    ) -> Iterable[Tuple[float, float]]:
        """Yield candidate points from the minimal neighborhood of cells covering `radius_m`."""
        # Degree deltas for a lat/lng window approximating the radius
        deg_lat = radius_m / 111_320.0  # meters per degree latitude
        cos_lat = max(0.01, math.cos(math.radians(lat)))
        deg_lng = radius_m / (111_320.0 * cos_lat)
        di = int(math.ceil(deg_lat / self.cell_deg))
        dj = int(math.ceil(deg_lng / self.cell_deg))
        ci, cj = self._key(lat, lng)
        for i in range(ci - di, ci + di + 1):
            for j in range(cj - dj, cj + dj + 1):
                for p in self._cells.get((i, j), []):
                    yield p


# ------------------------------
# Data models
# ------------------------------


@dataclass(frozen=True)
class ProximityResult:
    input_id: str
    footprint_within_m: int
    footprint_present_flag: bool


# ------------------------------
# Core logic
# ------------------------------


def _collect_footprint_files(patterns: Sequence[str]) -> List[str]:
    files: List[str] = []
    for pat in patterns:
        expanded = glob.glob(pat)
        if expanded:
            files.extend(expanded)
        else:
            if os.path.exists(pat):
                files.append(pat)
    if not files:
        raise FileNotFoundError(
            "No footprint files found for provided --footprints arguments."
        )
    return files


def build_index(footprint_paths: Sequence[str], cell_deg: float = 0.01) -> GridIndex:
    idx = GridIndex(cell_deg=cell_deg)
    total = 0
    for p in footprint_paths:
        pts = load_centroids_from_file(p)
        idx.add_many(pts)
        total += len(pts)
    print(
        f"Loaded {total} footprint centroids from {len(footprint_paths)} file(s).",
        flush=True,
    )
    return idx


def nearest_distance_m(
    idx: GridIndex, lat: float, lng: float, radius_m: float
) -> Tuple[bool, int]:
    """Return (present_flag, distance_m_rounded_or_neg1)."""
    best = None
    for clat, clng in idx.neighbors_within(lat, lng, radius_m):
        d = haversine_m(lat, lng, clat, clng)
        if best is None or d < best:
            best = d
            if best <= radius_m:
                break
    if best is None or best > radius_m:
        return (False, -1)
    return (True, int(round(best)))


def compute_proximity_for_rows(
    geocode_rows: List[Dict[str, str]],
    cfg: config_loader.Config,
    idx: GridIndex,
) -> List[ProximityResult]:
    results_by_ix: Dict[int, ProximityResult] = {}

    def worker(ix: int, row: Dict[str, str]) -> None:
        input_id = row.get("input_id", "")
        status = row.get("geocode_status", "")
        lat = _safe_float(row.get("lat", ""))
        lng = _safe_float(row.get("lng", ""))

        present = False
        dist = -1

        if status == "OK" and lat is not None and lng is not None:
            present, dist = nearest_distance_m(
                idx, lat, lng, float(cfg.thresholds.footprint_radius_m)
            )

        results_by_ix[ix] = ProximityResult(
            input_id=input_id,
            footprint_within_m=dist,
            footprint_present_flag=present,
        )

    with ThreadPoolExecutor(max_workers=cfg.concurrency.workers) as pool:
        futures = []
        for ix, r in enumerate(geocode_rows):
            futures.append(pool.submit(worker, ix, r))
        for f in as_completed(futures):
            f.result()

    # Reassemble in input order
    return [results_by_ix[i] for i in range(len(geocode_rows))]


def run_footprints(
    geocode_csv_path: str,
    output_csv_path: str,
    config_path: str,
    footprint_paths: Sequence[str],
    log_path: Optional[str] = None,
    cell_deg: float = 0.01,
) -> int:
    """Compute footprint proximity for all geocoded rows.

    Returns the number of processed rows (same as geocode rows).
    """
    cfg = config_loader.load_config(config_path)

    # Read geocode rows
    with open(geocode_csv_path, "r", encoding="utf-8", newline="") as f:
        geocode_rows = list(csv.DictReader(f))

    # Build index
    files = _collect_footprint_files(list(footprint_paths))
    idx = build_index(files, cell_deg=cell_deg)

    # Compute proximity
    results = compute_proximity_for_rows(geocode_rows, cfg, idx)

    # Ensure output directory exists
    out_dir = os.path.dirname(output_csv_path) or "."
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Deterministic write
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(
            f_out,
            fieldnames=["input_id", "footprint_within_m", "footprint_present_flag"],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "input_id": r.input_id,
                    "footprint_within_m": r.footprint_within_m,
                    "footprint_present_flag": _format_bool(r.footprint_present_flag),
                }
            )

    # Optional minimal JSONL log (counts only)
    if log_path:
        Path(os.path.dirname(log_path) or ".").mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f_log:
            summary = {
                "rows": len(results),
                "present_true": sum(1 for r in results if r.footprint_present_flag),
                "present_false": sum(
                    1 for r in results if not r.footprint_present_flag
                ),
            }
            f_log.write(json.dumps(summary) + "\n")

    return len(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Building‑footprint proximity computation."
    )
    parser.add_argument("--geocode", required=True, help="Path to data/geocode.csv")
    parser.add_argument(
        "--footprints",
        required=True,
        nargs="+",
        help="One or more file paths or globs to GeoJSON/NDJSON/CSV (lat,lng).",
    )
    parser.add_argument(
        "--output", required=True, help="Path to write data/footprints.csv"
    )
    parser.add_argument("--config", required=True, help="Path to config/config.yml")
    parser.add_argument(
        "--log",
        required=False,
        default="data/logs/footprints_log.jsonl",
        help="Path to JSONL log (default: data/logs/footprints_log.jsonl)",
    )
    parser.add_argument(
        "--celldeg",
        required=False,
        type=float,
        default=0.01,
        help="Grid cell size in degrees (default 0.01).",
    )
    args = parser.parse_args()

    count = run_footprints(
        geocode_csv_path=args.geocode,
        output_csv_path=args.output,
        config_path=args.config,
        footprint_paths=args.footprints,
        log_path=args.log,
        cell_deg=args.celldeg,
    )
    print(f"Computed proximity for {count} rows -> {args.output}")


if __name__ == "__main__":
    main()
