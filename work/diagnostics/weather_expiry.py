"""Offline impact diagnostic using the user's mission and controlled polygons.

No network or browser access; these polygons are test data, not NOAA forecasts.
The user's coordinates remain in memory and are not included in the report.
"""

import json
import sys

from server.solver.weather_impact import assess_impact, epoch

with open(sys.argv[1]) as stream:
    mission = json.load(stream)
env = mission["env"]
nodes = {node["id"]: node for key in ("airports", "targets") for node in env[key]}
route = ["A1", "T8", "T7", "T5", "T2", "T3", "T6", "A3"]
remaining = {"1": [[nodes[n]["lat"], nodes[n]["lng"]] for n in route]}


def cell(target_id):
    target = nodes[target_id]
    lat, lng = target["lat"], target["lng"]
    return {
        "rule": "No fly",
        "validFrom": "2026-10-12T02:00:00Z",
        "validTo": "2026-10-12T03:00:00Z",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [lng - .2, lat - .2], [lng + .2, lat - .2],
                [lng + .2, lat + .2], [lng - .2, lat + .2],
                [lng - .2, lat - .2],
            ]],
        },
    }


previous = [cell("T1"), cell("T4")]
missed = [nodes[n] for n in ("T1", "T4")]
start = epoch("2026-10-12T03:00:00Z")
end = epoch("2026-10-12T18:00:00Z")
results = {}
for name, retrieved in (
    ("expiry during accelerated playback", "2026-10-12T02:00:00Z"),
    ("expiry with observation at mission time", "2026-10-12T03:00:00Z"),
):
    results[name] = assess_impact(
        env, remaining, missed, previous, [], start, end, retrieved
    )

results["new obstacle on remaining route"] = assess_impact(
    env, remaining, missed, [], [cell("T5")],
    epoch("2026-10-12T02:30:00Z"), end, "2026-10-12T02:00:00Z",
)
assert results["expiry during accelerated playback"]["affected"]
assert results["expiry during accelerated playback"]["canReplan"]
assert results["expiry during accelerated playback"]["requiresWeatherReview"]
assert results["expiry with observation at mission time"]["affected"]
assert results["new obstacle on remaining route"]["affected"]
print(json.dumps({"kind": "offline controlled weather on imported mission", "results": results}, indent=2))
