"""Deterministic repro of the C2 replan DOUBLE-BACK / TK1↔C2-twice bug.

NOT a browser test — drives /api/solve_with_allocation directly, so the exact
sub-optimal replan reproduces every run with no mid-animation cutting.

Scenario: a mission is solved from A1; the drone refuels once at TK1 and is heading
TK1 → T2 when the mission is CUT (checkpoint C2-1 sits in the TK1→T2 segment). The
correct replan continues FORWARD (C2 → T2 → T11 → T8 → TK1 → …). Instead the
opportunistic DFS re-solves with no directional/momentum context and flies BACKWARD
first (C2 → T5 → T10 → T7 → TK1), then TK1 → T8 → T11 → T2 → TK1 — and because T2 is
adjacent to C2, the TK1↔C2 stretch is flown TWICE.

Observed raw solver output (the bug):
  route = [C2-1, T5, T10, T7, TK1, T8, T11, T2, TK1, T15, T16, T17, A1, T18, T13, A1]
  → C2↔TK1 traversed twice; a forward-continue route would avoid it.

ROOT: the DFS first leg from the cut node may go anywhere, including reversing back
toward the just-departed refuel base (TK1). It optimizes points-then-distance with no
penalty for reversing the drone's committed heading, and the double-back's two
traversals sit in different legs so per-leg distance accounting doesn't flag them.

FIX DIRECTION (next session): penalize / forbid the first post-cut leg from immediately
returning to the base the drone just departed, OR seed the cut node with its
"arrived-from" heading so a reversal costs. Verify against this script.

Run: python3 scripts/repro_c2_doubleback.py   (server must be up on :8893)
"""
import json
import os
import requests

# Self-contained: the env fixture is committed alongside this script, so the repro
# runs on any machine / clone. Override with ISR_REPRO_ENV or ISR_SERVER if needed.
ENV_FILE = os.environ.get(
    "ISR_REPRO_ENV",
    os.path.join(os.path.dirname(__file__), "fixtures", "repro_c2_env.json"),
)
SERVER = os.environ.get("ISR_SERVER", "http://localhost:8893")


def main():
    d = json.load(open(ENV_FILE))
    env = json.loads(json.dumps(d["env"]))
    dc = json.loads(json.dumps(d["drone_configs"]))

    # C2-1 in the TK1→T2 segment (30% from TK1 toward T2).
    env["checkpoints"] = [{"id": "C2-1", "lat": 32.36511, "lng": -90.00887}]
    # Targets already done before this cut (NOT T2/T11/T8, NOT T5/T10/T7).
    visited = ["T9", "T12", "T1", "T6", "T3", "T4"]
    env["visited_targets"] = visited

    c = dc["1"]
    c["start_airport"] = "C2-1"
    c["start_id"] = "C2-1"
    c["end_airport"] = "-"
    c["start_fuel"] = 60.0     # just refueled at TK1, flew a bit toward T2
    c["refuels_used"] = 1      # one refuel (TK1) already spent

    payload = {
        "env": env,
        "drone_configs": dc,
        "allocation_strategy": "efficient",
        "is_checkpoint_replan": True,
        "visited_targets": visited,
        "post_optimize": True,
    }
    r = requests.post(
        f"{SERVER}/api/solve_with_allocation", json=payload, timeout=120
    )
    j = r.json()
    rt = j.get("routes", {}).get("1", {})
    route = rt.get("route") or []
    print("route:", route)
    print("refuel_indices:", rt.get("refuel_indices"))
    print("distance:", round(rt.get("distance", 0), 1), "points:", rt.get("points"))

    # Assert the bug is GONE: the drone must not double back over TK1↔C2. A cheap
    # proxy — the first move out of C2-1 should head toward T2 (continue the leg), not
    # reverse to the T5/T10/T7 cluster. Tighten once the fix lands.
    if route[:2] == ["C2-1", "T5"]:
        print("\n❌ BUG PRESENT: first move is backward to T5 (double-back tangle).")
    else:
        print("\n✅ First move is not the backward detour — check full route for double-back.")


if __name__ == "__main__":
    main()
