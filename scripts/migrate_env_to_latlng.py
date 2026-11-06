#!/usr/bin/env python3
"""One-shot migration of legacy grid-unit environment JSONs to lat/lng + km.

Old schema:
    airports/targets: [{id, x, y, ...}]          (x,y in 0-100 grid)
    sams:             [{pos: [x,y], range, ...}] (range in grid units)
    drone_configs:    [{fuel_budget, ...}]       (grid units)
    distance_matrix:  [[grid]]                   (optional)

New schema:
    airports/targets: [{id, lat, lng, ...}]
    sams:             [{center: {lat, lng}, range_km, ...}]
    drone_configs:    [{fuel_budget_km, speed_kmh, ...}]
    distance_matrix_km: [[km]]                   (optional)

Conversion assumes the default SF bounds baked into the pre-migration code:
    {south: 37.70, north: 37.85, west: -122.51, east: -122.36}

Each converted file is written in place; the original is backed up with a
.bak extension. Running twice is a no-op — already-migrated files are
detected and skipped.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

SOUTH, NORTH = 37.70, 37.85
WEST, EAST = -122.51, -122.36
MID_LAT = (SOUTH + NORTH) / 2
KM_PER_DEG_LAT = 111.32


def km_per_deg_lng(ref_lat: float) -> float:
    return KM_PER_DEG_LAT * math.cos(math.radians(ref_lat))


# One grid unit (of the 0-100 grid) spans this many km on each axis.
# The grid is square in degrees (0.15 x 0.15), so:
GRID_DEG_SPAN = NORTH - SOUTH  # 0.15 for both axes by construction
KM_PER_GRID_UNIT_NS = (GRID_DEG_SPAN / 100.0) * KM_PER_DEG_LAT  # ~0.167
KM_PER_GRID_UNIT_EW = (GRID_DEG_SPAN / 100.0) * km_per_deg_lng(MID_LAT)  # ~0.132

# Average km per grid unit for scalar distance conversions (fuel, SAM range,
# distance matrix). Anisotropy is small at SF latitude; averaging is fine for
# seed data. After migration, all new values are native km — this factor is
# only used once.
KM_PER_GRID_UNIT = (KM_PER_GRID_UNIT_NS + KM_PER_GRID_UNIT_EW) / 2  # ~0.15


def grid_xy_to_latlng(x: float, y: float) -> tuple[float, float]:
    lat = SOUTH + (y / 100.0) * (NORTH - SOUTH)
    lng = WEST + (x / 100.0) * (EAST - WEST)
    return lat, lng


def already_migrated(env: dict) -> bool:
    """Detect new-schema files so the script is idempotent."""
    for key in ("airports", "targets"):
        items = env.get(key) or []
        if items and "lat" in items[0] and "lng" in items[0]:
            return True
    sams = env.get("sams") or []
    if sams and "center" in sams[0]:
        return True
    return False


def convert_airport_or_target(item: dict) -> dict:
    out = dict(item)
    if "x" in out and "y" in out:
        lat, lng = grid_xy_to_latlng(float(out.pop("x")), float(out.pop("y")))
        out["lat"] = lat
        out["lng"] = lng
    return out


def convert_sam(sam: dict) -> dict:
    out = dict(sam)
    pos = out.pop("pos", None) or out.pop("position", None)
    if pos is None and "x" in out and "y" in out:
        pos = [out.pop("x"), out.pop("y")]
    if pos is not None:
        lat, lng = grid_xy_to_latlng(float(pos[0]), float(pos[1]))
        out["center"] = {"lat": lat, "lng": lng}
    if "range" in out:
        out["range_km"] = float(out.pop("range")) * KM_PER_GRID_UNIT
    return out


def convert_drone_config(cfg: dict) -> dict:
    out = dict(cfg)
    if "fuel_budget" in out:
        out["fuel_budget_km"] = float(out.pop("fuel_budget")) * KM_PER_GRID_UNIT
    if "fuel_capacity" in out:
        out["fuel_capacity_km"] = float(out.pop("fuel_capacity")) * KM_PER_GRID_UNIT
    # Default speed for pre-migration drones: 120 km/h.
    out.setdefault("speed_kmh", 120.0)
    return out


def convert_distance_matrix(matrix) -> list[list[float]]:
    return [[float(v) * KM_PER_GRID_UNIT for v in row] for row in matrix]


def migrate_env(env: dict) -> dict:
    out = dict(env)

    if "airports" in out:
        out["airports"] = [convert_airport_or_target(a) for a in out["airports"]]
    if "targets" in out:
        out["targets"] = [convert_airport_or_target(t) for t in out["targets"]]
    if "sams" in out:
        out["sams"] = [convert_sam(s) for s in out["sams"]]

    # Drone configs may live at top level or nested.
    if "drone_configs" in out:
        dc = out["drone_configs"]
        if isinstance(dc, dict):
            out["drone_configs"] = {k: convert_drone_config(v) for k, v in dc.items()}
        elif isinstance(dc, list):
            out["drone_configs"] = [convert_drone_config(c) for c in dc]

    if "fuel_budget" in out:
        out["fuel_budget_km"] = float(out.pop("fuel_budget")) * KM_PER_GRID_UNIT

    if "distance_matrix" in out:
        out["distance_matrix_km"] = convert_distance_matrix(out.pop("distance_matrix"))

    return out


def migrate_file(path: Path) -> str:
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return f"SKIP {path}: cannot parse JSON ({e})"

    if not isinstance(data, dict):
        return f"SKIP {path}: top-level not an object"

    if already_migrated(data):
        return f"SKIP {path}: already in new schema"

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(path, backup)

    migrated = migrate_env(data)
    path.write_text(json.dumps(migrated, indent=2))
    return f"OK   {path}"


DEFAULT_TARGETS = [
    "webapp/editor/tmp_environment.json",
    "webapp/editor/mission_control_drone1.json",
]


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    targets = [Path(a) for a in argv[1:]] if len(argv) > 1 else [repo_root / p for p in DEFAULT_TARGETS]

    for t in targets:
        if not t.exists():
            print(f"SKIP {t}: file not found")
            continue
        print(migrate_file(t))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
