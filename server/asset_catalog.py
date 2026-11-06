"""Platform and sensor catalog (catalog/asset_catalog.json).

Nominal, public values for the aircraft a drone can be (MQ-9A, RQ-4B, ...)
and the sensors they carry. Choosing a platform and sensors fills a drone's
settings; every filled value stays editable in the Asset tab.

Units: the catalog uses km/h, hours, minutes and km. Drone configs use the
planner's units: speed km per MINUTE, loiter minutes, fuel (range) km.

Target categories (catalog "target_categories"): what each target letter
means — a: Personnel, b: Light vehicles, ... Each has an id (the letter stored
on targets and in drone settings), a name, examples and a map colour. A
mission may rename them or add its own (env.target_categories).

Sensors and categories: for each category the drone uses its BEST sensor for
it — the largest detection radius, with that sensor's loiter time (ties: the
shorter loiter). A category none of its sensors can see is not visited.
Carrying more sensors therefore never makes a drone worse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog" / "asset_catalog.json"
# Categories a catalog without "target_categories" has (older catalogs).
DEFAULT_CATEGORY_IDS = ("a", "b", "c", "d", "e")
MINUTES_PER_HOUR = 60.0
_RUNWAYS = ("S", "M", "L")


class CatalogError(ValueError):
    """The catalog file breaks a rule (message says which)."""


def validate(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Check the catalog's rules; returns it unchanged or raises CatalogError."""
    sensors = catalog.get("sensors") or []
    platforms = catalog.get("platforms") or []
    categories = catalog.get("target_categories") or []
    cat_ids = [c.get("id") for c in categories]
    if any(not i for i in cat_ids):
        raise CatalogError("every target category needs an id")
    if len(set(cat_ids)) != len(cat_ids):
        raise CatalogError(
            f"duplicate target category id(s): {sorted({i for i in cat_ids if cat_ids.count(i) > 1})}"
        )
    known = set(category_ids(catalog))
    sensor_ids = [s.get("id") for s in sensors]
    platform_ids = [p.get("id") for p in platforms]
    for kind, ids in (("sensor", sensor_ids), ("platform", platform_ids)):
        if any(not i for i in ids):
            raise CatalogError(f"every {kind} needs an id")
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise CatalogError(f"duplicate {kind} id(s): {sorted(dup)}")
    for s in sensors:
        types = s.get("types") or {}
        if not types:
            raise CatalogError(f"sensor {s['id']} lists no target types")
        for t, v in types.items():
            if t != "all" and t not in known:
                raise CatalogError(f"sensor {s['id']}: unknown target category {t!r}")
            if (
                float(v.get("detection_km", -1)) < 0
                or float(v.get("loiter_min", -1)) < 0
            ):
                raise CatalogError(
                    f"sensor {s['id']} type {t}: detection_km and loiter_min must be >= 0"
                )
    for p in platforms:
        for key in ("cruise_altitude_km", "cruise_speed_kmh", "endurance_h"):
            if not float(p.get(key) or 0) > 0:
                raise CatalogError(f"platform {p['id']}: {key} must be > 0")
        for key in ("range_km", "max_speed_kmh", "operational_range_km", "ceiling_km"):
            if key in p and not float(p.get(key) or 0) > 0:
                raise CatalogError(f"platform {p['id']}: {key} must be > 0 when given")
        if p.get("runway", "S") not in _RUNWAYS:
            raise CatalogError(f"platform {p['id']}: runway must be one of {_RUNWAYS}")
        unknown = [s for s in p.get("sensors") or [] if s not in sensor_ids]
        if unknown:
            raise CatalogError(
                f"platform {p['id']} carries unknown sensor(s): {unknown}"
            )
    return catalog


def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    with open(path or CATALOG_PATH) as f:
        return validate(json.load(f))


def _by_id(items: List[Dict[str, Any]], item_id: str, kind: str) -> Dict[str, Any]:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise CatalogError(f"unknown {kind}: {item_id!r}")


def category_ids(catalog: Dict[str, Any]) -> List[str]:
    """The catalog's target category ids, in order (a, b, c, ...)."""
    cats = catalog.get("target_categories") or []
    return [str(c["id"]) for c in cats if c.get("id")] or list(DEFAULT_CATEGORY_IDS)


def catalog_platform_sensors(catalog: Dict[str, Any], platform_id: str) -> List[str]:
    """Every sensor a platform carries (its default load)."""
    return list(
        _by_id(catalog.get("platforms") or [], platform_id, "platform").get("sensors")
        or []
    )


def sensor_for_type(sensor: Dict[str, Any], ttype: str) -> Optional[Dict[str, float]]:
    """(detection_km, loiter_min) this sensor gives on a target type, or None."""
    types = sensor.get("types") or {}
    v = types.get(ttype, types.get("all"))
    return (
        None
        if v is None
        else {
            "detection_km": float(v["detection_km"]),
            "loiter_min": float(v["loiter_min"]),
        }
    )


def drone_fields(
    catalog: Dict[str, Any],
    platform_id: str,
    sensor_ids: List[str],
    categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Drone config fields for a platform carrying these sensors.

    `categories`: the mission's category ids (default: the catalog's). A
    category the mission added that no sensor lists is not visited.
    Returns the planner's units (speed km/min, fuel = range km, loiter min).
    """
    platform = _by_id(catalog.get("platforms") or [], platform_id, "platform")
    allowed = set(platform.get("sensors") or [])
    sensors = []
    for sid in sensor_ids:
        if sid not in allowed:
            raise CatalogError(f"{platform_id} does not carry sensor {sid!r}")
        sensors.append(_by_id(catalog.get("sensors") or [], sid, "sensor"))

    speed_kmh = float(platform["cruise_speed_kmh"])
    access, radius, loiter = {}, {}, {}
    for t in categories or category_ids(catalog):
        options = [o for o in (sensor_for_type(s, t) for s in sensors) if o is not None]
        if not options:
            access[t], radius[t], loiter[t] = False, 0.0, 0.0
            continue
        best = max(options, key=lambda o: (o["detection_km"], -o["loiter_min"]))
        access[t], radius[t], loiter[t] = True, best["detection_km"], best["loiter_min"]

    return {
        "platform_id": platform_id,
        "sensor_ids": list(sensor_ids),
        "cruise_altitude_km": float(platform["cruise_altitude_km"]),
        # The aircraft's ceiling: not a setting, a limit the app warns against.
        "ceiling_km": (
            float(platform["ceiling_km"]) if platform.get("ceiling_km") else None
        ),
        "climb_angle_deg": float(platform.get("climb_angle_deg") or 12),
        "descent_angle_deg": float(
            platform.get("descent_angle_deg") or platform.get("climb_angle_deg") or 12
        ),
        "speed": speed_kmh / MINUTES_PER_HOUR,
        # Fuel = the published range when there is one, else cruise x endurance.
        "fuel_budget": round(
            float(
                platform.get("range_km") or speed_kmh * float(platform["endurance_h"])
            ),
            1,
        ),
        "runway": platform.get("runway", "S"),
        "target_access": access,
        "detection_radius": radius,
        "loiter_times": loiter,
        "loiter_enabled": any(v > 0 for v in loiter.values()),
    }
