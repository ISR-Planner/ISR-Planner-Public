# ISR Planner Demo Package

Demo materials for a 15-minute presentation of the ISR Mission Planner.

## Contents

| File | Purpose |
|------|---------|
| `presentation_script.md` | Full script with timing, talking points, and demo flow |
| `demo_01_basics.json` | Simple scenario: 2 drones, 8 targets, 2 SAMs |
| `demo_02_complex.json` | Complex scenario: 5 drones, 20 targets, refueling, constraints |
| `demo_03_agent.json` | Agent interaction: includes sample prompts |
| `demo_04_dynamic.json` | Dynamic replanning: designed for cut/edit demo |

## Quick Start

1. **Start the server**
   ```bash
   ./run_planner.sh
   ```

2. **Open browser** to http://localhost:8893

3. **Hard-refresh before importing** — **Cmd+Shift+R** on Mac or **Ctrl+Shift+R** on Windows/Linux. Do this on both the local server and Railway, before each fresh demo import or switch to another demo. It reloads the latest scripts and starts a fresh page so the import initializes without carrying over the previous run's in-memory state.

4. **Load a demo file**
   - Click "Load" or drag-drop JSON file onto the planner

5. **Follow the script** in `presentation_script.md`

## Demo Progression

```
demo_01_basics.json    →  Core concepts (2 min)
demo_02_complex.json   →  Multi-drone + optimizer (3 min)
demo_03_agent.json     →  Agent interaction (3 min)
demo_04_dynamic.json   →  Dynamic missions (3 min)
```

## Key Points to Emphasize

1. **Unique Architecture**
   - Exact solver (Held-Karp) + optimizer pipeline = fast near-optimal at scale

2. **Real-World Simulation**
   - Lat/lng coordinates, haversine distances
   - Target types/priorities, drone capabilities, runway constraints

3. **Two Solving Pathways**
   - Manual controls: optimal within constraints
   - LLM Agent: natural language flexibility

4. **Dynamic Missions**
   - Solve → Animate → Edit → Cut → Replan → Continue

5. **Next Steps**
   - Agent-driven dynamic environments
   - Multi-stop refueling + tankers
   - Experiential learning
   - Refactor & DOW delivery

## Troubleshooting

**Server won't start**: Check PYTHONPATH and port 8893 availability
**Agent not responding**: Verify ANTHROPIC_API_KEY in .env
**JSON won't load**: Validate JSON syntax (check for trailing commas)

## Pre-Presentation Checklist

- [ ] Server running
- [ ] Hard-refresh before each fresh demo import, locally or on Railway: **Cmd+Shift+R** (Mac) / **Ctrl+Shift+R** (Windows/Linux), then load the JSON.
- [ ] All 4 demo JSONs load successfully
- [ ] Agent responds to test query
- [ ] Browser zoom appropriate for screen share
- [ ] Backup: can describe features verbally if demo fails
