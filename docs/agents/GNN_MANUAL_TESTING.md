# Testing the neural planners by hand

A click-through guide for evaluating the GNN planners against the existing
solver. No command line needed after startup.

## 1. Start the server

```bash
./run_planner.sh
```

Then open <http://localhost:8893>. Before every fresh environment import, it is
advisable to hard refresh: **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux).
This also applies on Railway: reload current scripts and start a fresh page, then
import to initialize the mission and help prevent prior-run state carrying over.

## 2. Load an environment

Use the **Env** tab to load or generate a scenario (airports, targets, SAM
zones, drones). Everything else operates on whatever is currently loaded.

## 3. Open the GNN tab

The tab lists the trained checkpoints it found in `models/`, showing epochs and
final training loss for each.

There are two independent model slots, because the planner makes two separate
decisions:

| Slot | Decides | Set to "None" |
|---|---|---|
| **Assignment model** | which drone gets which target | falls back to a greedy heuristic |
| **Route model** | what order each drone visits its targets | falls back to greedy nearest-feasible |

Leaving both on "None" runs the system with no neural network at all — useful as
a sanity check.

## 4. Press a button

| Button | What it does |
|---|---|
| **Compare all planners** | The one to use. Runs all three planners on the same environment and shows them side by side. |
| **Run GNN only** | Runs just the GNN and puts its plan on the map. |
| **Inspect graph** | Shows the graph the network actually sees — node/edge counts, feature names, and which targets were excluded for sitting inside SAM rings. |

### The three planners compared

- **Existing planner (exact solver)** — the current production system, and the
  control. Deterministic allocator plus an exact Held-Karp route optimizer.
- **GNN allocation + exact solver** — the network chooses target assignments,
  the exact solver still builds routes.
- **GNN end-to-end (neural routing)** — the network does both. This is the only
  configuration with no exponential solver in the loop.

## 5. Read the results

The comparison table shows time, points, targets hit, fuel used, and whether the
solution is fuel-feasible. Best values in each column are highlighted green, and
**Show on map** draws any planner's routes on the mission map.

**Read the fuel-feasible column first.** Points are only comparable between rows
marked *Yes*. A planner that overruns its fuel budget can score more points while
flying a mission the drones cannot actually complete — this is exactly how the
LLM agents appeared to outperform the solver until validity was accounted for.

## What to expect

On small scenarios all three usually find similar point totals, and the exact
solver often finds a shorter route for the same score. The neural end-to-end
planner's advantage shows up as **targets per drone rises**, where the exact
solver's cost grows exponentially (it enumerates every subset of targets) while
the network's stays flat.

Two known limitations worth checking rather than being surprised by:

- **Above roughly 10 drones the assignment model degrades**, and around 50 it
  collapses — assigning every target to one drone that may not be able to reach
  them, producing an empty mission. It was trained on 2–6 drone scenarios.
- **Total runtime is dominated by the SAM-aware distance matrix**, not by
  routing. Both approaches pay that cost, so wall-clock differences between them
  are much smaller than the routing-time differences would suggest.

Full measurements: `results/GNN_COMPARISON_REPORT.md`.

## If something looks wrong

- **"No trained models found"** — `models/` is empty. Train with
  `python scripts/train_gnn.py data/gnn/*.jsonl --output models/gnn_assign.pt`.
- **A model is greyed out as UNREADABLE** — it was trained against an older
  feature schema and needs retraining.
- **Every planner scores 0** — the environment is likely infeasible (targets
  unreachable within fuel budget, or all inside SAM rings). Use **Inspect
  graph** to see what was excluded.
