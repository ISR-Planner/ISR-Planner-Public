#!/usr/bin/env python3
"""Test the /api/agents/compare endpoint against a running planner instance.

Usage:
    # Start the planner first: ./run_planner.sh
    python3 scripts/test_agent_compare.py [--port 8893]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import requests

DEFAULT_ENV = {
    "airports": [
        {"id": "A1", "lat": 32.3113, "lng": -90.1760},
        {"id": "A2", "lat": 32.3500, "lng": -90.2100},
    ],
    "targets": [
        {"id": "T1", "lat": 32.3300, "lng": -90.1900, "priority": 8, "type": "a"},
        {"id": "T2", "lat": 32.3400, "lng": -90.1650, "priority": 6, "type": "b"},
        {"id": "T3", "lat": 32.3200, "lng": -90.2050, "priority": 9, "type": "a"},
        {"id": "T4", "lat": 32.3550, "lng": -90.1800, "priority": 4, "type": "c"},
        {"id": "T5", "lat": 32.3050, "lng": -90.1950, "priority": 7, "type": "a"},
    ],
    "sams": [
        {"center": {"lat": 32.3350, "lng": -90.1850}, "range_km": 1.0},
    ],
    "drone_configs": {
        "1": {
            "enabled": True,
            "fuel_budget_km": 50,
            "speed_kmh": 120,
            "start_airport": "A1",
            "end_airport": "-",
        },
        "2": {
            "enabled": True,
            "fuel_budget_km": 50,
            "speed_kmh": 120,
            "start_airport": "A2",
            "end_airport": "-",
        },
    },
}

DEFAULT_DRONE_CONFIGS = DEFAULT_ENV["drone_configs"]

DEFAULT_MESSAGE = "Plan a mission to maximize priority points. Visit as many targets as possible."


def run_test(port: int, message: str):
    url = f"http://localhost:{port}/api/agents/compare"
    payload = {
        "message": message,
        "env": DEFAULT_ENV,
        "drone_configs": DEFAULT_DRONE_CONFIGS,
    }

    print(f"Sending comparison request to {url}...")
    print(f"  Message: {message}")
    print(f"  Env: {len(DEFAULT_ENV['targets'])} targets, {len(DEFAULT_ENV['sams'])} SAMs, {len(DEFAULT_ENV['airports'])} airports")
    print(f"  Drones: {len(DEFAULT_DRONE_CONFIGS)} enabled")
    print()

    t0 = time.time()
    try:
        resp = requests.post(url, json=payload, timeout=300)
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to localhost:{port}. Is the planner running?")
        return False

    elapsed = time.time() - t0
    print(f"Response received in {elapsed:.1f}s (HTTP {resp.status_code})")
    print()

    if resp.status_code != 200:
        print(f"ERROR: {resp.text[:500]}")
        return False

    data = resp.json()

    # Print summary
    for agent_key in ["reasoning", "langgraph_v4"]:
        m = data.get(agent_key, {})
        print(f"=== {agent_key.upper()} ===")
        print(f"  Latency:         {m.get('latency_s', '?')}s")
        print(f"  LLM Calls:       {m.get('llm_calls', '?')}")
        print(f"  Iterations:      {m.get('iterations', '?')}")
        print(f"  Valid Solution:  {m.get('valid_solution', '?')}")
        print(f"  Total Points:    {m.get('total_points', '?')}")
        print(f"  Fuel Used:       {m.get('total_fuel_km', '?')} km")
        print(f"  Fuel Efficiency: {m.get('fuel_efficiency', '?')}")
        print(f"  Targets Visited: {m.get('targets_visited', '?')} / {m.get('targets_total', '?')}")
        print(f"  Coverage:        {m.get('target_coverage', '?')}")
        if m.get("error"):
            print(f"  ERROR:           {m['error']}")
        print()

    # Winners
    winners = data.get("winner", {})
    print("=== WINNERS ===")
    for metric, winner in winners.items():
        print(f"  {metric:20s} → {winner}")

    # Save full result
    output_path = f"scripts/comparison_result_{int(time.time())}.json"
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nFull result saved to {output_path}")

    return True


def main():
    parser = argparse.ArgumentParser(description="Test agent comparison endpoint")
    parser.add_argument("--port", type=int, default=8893)
    parser.add_argument("--message", type=str, default=DEFAULT_MESSAGE)
    args = parser.parse_args()

    success = run_test(args.port, args.message)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
