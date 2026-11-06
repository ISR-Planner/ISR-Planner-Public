"""Package a private original mission with a public NOAA capture for local replay."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mission", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--weather",
        type=Path,
        default=Path("webapp/_weather_diagnostics/noaa_20261012T012337Z.json"),
    )
    args = parser.parse_args()
    mission = json.loads(args.mission.read_text())
    weather = json.loads(args.weather.read_text())
    if mission.get("schema") != "isr_env_v1" or mission.get("is_segmented"):
        raise ValueError("Use the original unsegmented mission environment.")
    points = mission["env"]["airports"] + mission["env"]["targets"]
    start = datetime.fromisoformat(weather["retrievedAt"]).astimezone(timezone.utc)
    start = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    bundle = {
        "schema": "isr_weather_demo_v1",
        "name": "West to Northeast to Gulf — recorded NOAA",
        "description": (
            "Original mission with saved NOAA advisories and original validity times. "
            "Initial avoidance, weather checkpoint, review and replan demonstration. "
            "Expired advisories are not proof of clear weather."
        ),
        "mission": mission,
        "weather": weather,
        "window": {
            "start": start.isoformat(),
            "end": (start + timedelta(hours=16)).isoformat(),
            "timezone": "America/Chicago",
        },
        "bounds": [
            [min(p["lat"] for p in points) - 5, min(p["lng"] for p in points) - 5],
            [max(p["lat"] for p in points) + 5, max(p["lng"] for p in points) + 5],
        ],
        "policy": {"safety_margin_km": 5},
        "playback": {"speed": 1280},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, data in [
        ("01_Recorded_West_Northeast_Gulf.weather-demo.json", bundle),
        ("02_Live_West_Northeast_Gulf.environment.json", mission),
    ]:
        path = args.output / filename
        path.write_text(json.dumps(data, indent=2) + "\n")
        print(path)


if __name__ == "__main__":
    main()
