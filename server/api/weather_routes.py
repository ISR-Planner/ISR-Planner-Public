"""Same-origin access to current NOAA aviation advisories (one-minute cache)."""

import asyncio
import copy
import math
from datetime import datetime, timezone
from time import monotonic

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter()
_lock = asyncio.Lock()
_cached = None
_cached_at = 0.0
BASE = "https://aviationweather.gov/api/data/"
NAMES = {
    "TS": "Thunderstorms",
    "CONV": "Convective thunderstorms",
    "VA": "Volcanic ash",
    "TC": "Tropical cyclone",
    "TURB": "Turbulence",
    "ICE": "Icing",
    "TURB-HI": "High-level turbulence",
    "TURB-LO": "Low-level turbulence",
    "IFR": "Low ceiling / visibility",
    "MT_OBSC": "Mountain obscuration",
    "SFC_WND": "Strong surface wind",
    "LLWS": "Low-level wind shear",
}


def fetch_feed(product, hour=None):
    params = {"format": "geojson"}
    if hour is not None:
        params["fore"] = hour
    response = requests.get(
        BASE + product,
        params=params,
        timeout=(5, 20),
        headers={
            "User-Agent": "ISRPlanner-WeatherPreview/0.2",
            "Accept": "application/geo+json, application/json",
        },
    )
    response.raise_for_status()
    data = (
        {"type": "FeatureCollection", "features": []}
        if response.status_code == 204
        else response.json()
    )
    if data.get("type") != "FeatureCollection" or not isinstance(
        data.get("features"), list
    ):
        raise ValueError("Invalid NOAA GeoJSON response")
    return {"product": product, "url": response.url, "data": data}


def _normalize_polygon_geometry(geometry):
    """Close NOAA's implicit polygon rings without changing their boundary.

    Some SIGMET polygons list only their distinct vertices, including triangles.
    GeoJSON consumers expect the first position repeated at the end. Reject
    genuinely unusable boundaries rather than dropping an advisory from a feed.
    """
    result = copy.deepcopy(geometry)
    coordinates = result.get("coordinates")
    polygons = [coordinates] if result["type"] == "Polygon" else coordinates
    if not isinstance(polygons, list) or not polygons:
        raise ValueError("NOAA weather has invalid polygon coordinates")
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("NOAA weather has invalid polygon coordinates")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 3:
                raise ValueError("NOAA weather has invalid polygon coordinates")
            for position in ring:
                if (
                    not isinstance(position, list)
                    or len(position) < 2
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        for value in position[:2]
                    )
                    or abs(position[0]) > 540
                    or abs(position[1]) > 90
                ):
                    raise ValueError("NOAA weather has invalid polygon coordinates")
            if len({tuple(position[:2]) for position in ring}) < 3:
                raise ValueError("NOAA weather has a degenerate polygon boundary")
            if ring[0][:2] != ring[-1][:2]:
                ring.append(copy.deepcopy(ring[0]))
    return result


def normalize(feeds, retrieved_at):
    cells, forecast_times, excluded = [], set(), 0
    for feed in feeds:
        for feature in feed["data"]["features"]:
            geometry = feature.get("geometry") or {}
            props = feature.get("properties") or {}
            valid_at = props.get("validTime")
            if feed["product"] == "gairmet" and valid_at:
                forecast_times.add(valid_at)
            if geometry.get("type") not in ("Polygon", "MultiPolygon"):
                excluded += 1
                continue
            geometry = _normalize_polygon_geometry(geometry)
            rings = (
                [geometry["coordinates"]]
                if geometry["type"] == "Polygon"
                else geometry["coordinates"]
            )
            coords = [p for polygon in rings for ring in polygon for p in ring]
            if not coords:
                continue
            hazard = props.get("hazard", "Unknown")
            raw = (
                props.get("rawSigmet")
                or props.get("dueTo")
                or props.get("due_to")
                or ""
            )
            severe = (
                props.get("qualifier") == "SEV"
                or props.get("severity") == "SEV"
                or "SEV TURB" in raw
                or "SEV ICE" in raw
            )
            blocked = (
                hazard in ("TS", "CONV", "VA", "TC")
                or hazard in ("TURB", "ICE")
                and severe
            )

            def bound(key):
                value = props.get(key)
                if value is None:
                    return "Not supplied"
                return (
                    f"{value} ft (source)"
                    if feed["product"] == "sigmet"
                    else f"{value} (source code)"
                )

            cells.append(
                {
                    "id": f"W{len(cells)+1}",
                    "name": NAMES.get(hazard, hazard),
                    "rule": "No fly" if blocked else "Transit allowed",
                    "band": "Lower: " + bound("base") + " · Upper: " + bound("top"),
                    "geometry": geometry,
                    "properties": props,
                    "raw": raw,
                    "box": [
                        min(p[0] for p in coords),
                        min(p[1] for p in coords),
                        max(p[0] for p in coords),
                        max(p[1] for p in coords),
                    ],
                    "validAt": valid_at,
                    "validFrom": props.get("validTimeFrom"),
                    "validTo": props.get("validTimeTo"),
                    "product": feed["product"].upper(),
                    "identity": props.get("seriesId")
                    or props.get("tag")
                    or str(len(cells) + 1),
                    "sourceUrl": feed["url"],
                }
            )
    return {
        "retrievedAt": retrieved_at,
        "cells": cells,
        "forecastTimes": sorted(forecast_times),
        "excludedLines": excluded,
        "sources": [f["url"] for f in feeds],
    }


@router.get("/api/weather/current")
async def current_weather():
    global _cached, _cached_at
    async with _lock:
        if _cached is None or monotonic() - _cached_at >= 60:
            try:
                feeds = await asyncio.gather(
                    asyncio.to_thread(fetch_feed, "sigmet"),
                    *(
                        asyncio.to_thread(fetch_feed, "gairmet", hour)
                        for hour in (0, 3, 6, 9, 12)
                    ),
                )
                _cached = normalize(feeds, datetime.now(timezone.utc).isoformat())
                _cached_at = monotonic()
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                raise HTTPException(
                    502,
                    "Could not retrieve current NOAA advisories. Previous weather is not refreshed; retry shortly.",
                ) from exc
        return JSONResponse(
            copy.deepcopy(_cached), headers={"Cache-Control": "no-store"}
        )


class WeatherImpactRequest(BaseModel):
    env: dict
    remaining_routes: dict
    missed_targets: list
    previous: list
    current: list
    start: float
    end: float
    retrieved_at: str
    remaining_legs: dict = Field(default_factory=dict)


@router.post("/api/weather/impact")
def weather_impact(req: WeatherImpactRequest):
    from server.solver.weather_impact import assess_impact

    try:
        return assess_impact(
            req.env,
            req.remaining_routes,
            req.missed_targets,
            req.previous,
            req.current,
            req.start,
            req.end,
            req.retrieved_at,
            remaining_legs=req.remaining_legs,
        )
    except (ValueError, KeyError, TypeError):
        return {
            "affected": True,
            "canReplan": False,
            "reasons": [
                "Weather coverage or geometry cannot be verified for the remaining mission."
            ],
        }
