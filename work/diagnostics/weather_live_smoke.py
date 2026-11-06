"""Fetch real NOAA feeds and solve a private mission without browser automation.

Run from the repository root with its Python environment, for example:
  venv/bin/python -m work.diagnostics.weather_live_smoke /path/to/mission.json

Only public weather and a geometry-free test report are written by this script.
The mission is read into memory and sent to a direct Python solver subprocess.
"""

import argparse
import asyncio
import copy
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from server.api.weather_routes import current_weather


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mission", type=Path)
    parser.add_argument(
        "--capture", type=Path, default=Path("/tmp/isr-live-weather-capture.json")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("/tmp/isr-live-weather-result.json")
    )
    parser.add_argument("--hours", type=int, default=18)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    mission = json.loads(args.mission.read_text())
    started = monotonic()
    response = asyncio.run(current_weather())
    weather = json.loads(response.body)
    retrieval_seconds = monotonic() - started
    args.capture.parent.mkdir(parents=True, exist_ok=True)
    args.capture.write_text(json.dumps(weather, indent=2) + "\n")
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end = start + timedelta(hours=args.hours)
    env = copy.deepcopy(mission["env"])
    points = env.get("airports", []) + env.get("targets", [])
    west = min(p["lng"] for p in points) - 5
    east = max(p["lng"] for p in points) + 5
    south = min(p["lat"] for p in points) - 5
    north = max(p["lat"] for p in points) + 5
    matched = [
        c
        for c in weather["cells"]
        if c["box"][2] >= west
        and c["box"][0] <= east
        and c["box"][3] >= south
        and c["box"][1] <= north
    ]
    selected = [
        c
        for c in matched
        if (
            start <= instant(c["validAt"]) <= end
            if c.get("validAt")
            else instant(c["validFrom"]) < end and instant(c["validTo"]) > start
        )
    ]
    obstacles = []
    for cell in selected:
        if cell["rule"] != "No fly":
            continue
        geometry = cell["geometry"]
        polygons = (
            [geometry["coordinates"]]
            if geometry["type"] == "Polygon"
            else geometry["coordinates"]
        )
        for index, rings in enumerate(polygons):
            ring = rings[0]
            obstacles.append(
                {
                    "id": f"WX-{cell['id']}-{index}",
                    "center": {"lat": ring[0][1], "lng": ring[0][0]},
                    "range": 0,
                    "range_km": 0,
                    "weather_ring": [p[:2] for p in ring],
                }
            )
    env["sams"] = [
        s for s in env.get("sams", []) if not s.get("weather_ring")
    ] + obstacles
    env["weather_snapshot"] = {
        "retrievedAt": weather["retrievedAt"],
        "start": start.timestamp() * 1000,
        "end": end.timestamp() * 1000,
        "bounds": [[south, west], [north, east]],
        "safety_margin_km": 5,
        "policy": "mission-window union",
        "advisories": selected,
    }
    request = {
        "operation": "solve",
        "arguments": {
            "env": env,
            "drone_configs": mission["drone_configs"],
            "allocation_strategy": "geographic",
            "use_sam_aware_distances": True,
            "post_optimize": True,
        },
    }
    report = {
        "test": "Live NOAA retrieval and real in-process solver",
        "browser_verified": False,
        "retrieved_at": weather["retrievedAt"],
        "retrieval_seconds": round(retrieval_seconds, 3),
        "source_urls": weather["sources"],
        "total_advisories": len(weather["cells"]),
        "products": dict(Counter(c["product"] for c in weather["cells"])),
        "forecast_times": weather["forecastTimes"],
        "mission_start": start.isoformat(),
        "mission_end": end.isoformat(),
        "advisories_in_mission_area": len(matched),
        "advisories_in_selected_window": len(selected),
        "no_fly_polygons": len(obstacles),
        "no_fly_validity": [
            {
                "identity": c["identity"],
                "hazard": c["name"],
                "valid_from": c.get("validFrom"),
                "valid_to": c.get("validTo"),
            }
            for c in selected
            if c["rule"] == "No fly"
        ],
        "safety_margin_km": 5,
        "coverage_note": "The requested mission window does not extend NOAA validity. No advisories does not mean clear weather.",
    }
    print(
        json.dumps({"phase": "NOAA retrieved; solving", **report}, indent=2), flush=True
    )
    started = monotonic()
    try:
        process = subprocess.run(
            [sys.executable, "-m", "work.diagnostics.weather_backend_worker"],
            input=json.dumps(request) + "\n",
            text=True,
            capture_output=True,
            timeout=args.timeout,
            check=True,
        )
        solved = json.loads(process.stdout)
        report["solver_seconds"] = round(monotonic() - started, 3)
        report["success"] = bool(solved.get("success"))
        report["errors"] = solved.get("errors", [])
        report["routes"] = {
            str(did): {
                "route": route.get("route"),
                "distance_km": route.get("distance"),
                "anim_frame_points": len(
                    (route.get("anim_frame") or {}).get("trajectory", [])
                ),
                "anim_frame_total_km": (route.get("anim_frame") or {}).get(
                    "total_distance"
                ),
            }
            for did, route in solved.get("routes", {}).items()
        }
        report["server_route_verification_passed"] = bool(solved.get("success"))
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        report["success"] = False
        report["solver_seconds"] = round(monotonic() - started, 3)
        report["errors"] = [type(exc).__name__ + ": " + str(exc)]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
