# ISR Planner Presentation Script (15 Minutes)

> **Audience**: Mixed (technical + leadership + users)
> **Format**: Live demo with screen sharing
> **Key Message**: Fast, near-optimal multi-drone mission planning with AI-assisted dynamic replanning

**Before each fresh import:** Hard-refresh with **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux), then load the demo JSON. Use this sequence on both the local server and Railway, including when switching demos. It reloads the latest scripts and starts a fresh page so the import initializes without carrying over the previous run's in-memory state.

---

## Timing Overview

| Section | Duration | Cumulative |
|---------|----------|------------|
| 1. Introduction | 1:30 | 1:30 |
| 2. Basic Demo (Simple Solve) | 2:30 | 4:00 |
| 3. Multi-Drone & Optimizer | 3:00 | 7:00 |
| 4. Agent Interaction | 3:00 | 10:00 |
| 5. Dynamic Mission (Cut/Replan) | 3:00 | 13:00 |
| 6. Next Steps & Q&A | 2:00 | 15:00 |

---

## Section 1: Introduction (1:30)

**[Show: ISR Planner main screen with empty map]**

> "This is the ISR Mission Planner — a tool that allows ISR officers to create, simulate, and run mission scenarios.

> What makes this planner unique is the combination of an **exact orienteering solver** with an **optimization pipeline**. This gives us fast solutions for large numbers of targets across multiple drones, with near-optimal results.

> The planner uses **real maps** with latitude/longitude coordinates and haversine distances. Everything you see here can represent an actual mission scenario.

> Let me show you how it works."

---

## Section 2: Basic Demo — Simple Solve (2:30)

**[Hard-refresh, then load: demo_01_basics.json]**

> "Here's a simple scenario: 3 airports, 8 targets with different priorities, 2 SAM threat zones, and 2 drones.

> Each **target** has a type and priority — higher priority means more valuable. Each **drone** has a fuel budget measured in kilometers — that's its maximum range.

> Watch what happens when I solve."

**[Click: Solve]**

> "The planner allocates targets to drones, finds optimal routes avoiding SAM zones, and maximizes the total priority collected within fuel constraints.

> Notice the routes go **around** the SAM zones, not through them. The distance calculations account for this."

**[Click: Animate]**

> "We can animate the mission to visualize how it plays out."

**[Pause animation after a few seconds]**

> "Now let me show you the real power — scaling up."

---

## Section 3: Multi-Drone & Optimizer Pipeline (3:00)

**[Hard-refresh, then load: demo_02_complex.json]**

> "Here's a more realistic scenario: 4 airports, 20 targets, SAM coverage, and 5 drones with different capabilities.

> Some drones can only visit certain target types. Some have longer range. Some can refuel."

**[Show drone config panel briefly]**

> "The solver works in two phases:
> 1. **Held-Karp exact solver** optimally routes a subset of targets per drone
> 2. **Optimizer pipeline** adds missed targets and improves the solution

> The optimizer has three stages: INSERT missed targets, SWAP targets between drones, and remove route CROSSINGS."

**[Click: Solve with Allocation]**

> "Watch the solution appear..."

**[Point out: targets visited, routes, any drone differences]**

> "The allocation algorithm distributed targets based on geography. Different algorithms — geographic, greedy, balanced — can produce slightly different results.

> Knowing which works best for your scenario is where experience matters. We're building learning capabilities into the agent to recommend the best approach."

---

## Section 4: Agent Interaction (3:00)

**[Keep the same environment, or hard-refresh and then load demo_03_agent.json]**

> "Now let me show you the AI agent. Instead of clicking buttons, I can describe what I want in natural language."

**[Open Agent Chat]**

**[Type: "Solve this mission but start D1 at T5 and skip any targets with priority less than 3"]**

> "I've added constraints that would be difficult to express with buttons alone. The agent interprets this and translates it to solver instructions."

**[Wait for agent response and solution]**

> "The agent has access to all the same solving tools, but understands natural language. This enables much more flexible mission planning."

**[Type: "What's the fuel margin for D2?"]**

> "I can also ask questions about the solution. The agent knows the current mission state."

**[Show agent response]**

> "This conversational interface makes the planner accessible to officers who think in mission terms, not software terms."

---

## Section 5: Dynamic Mission — Cut & Replan (3:00)

**[Continue with the current environment, or hard-refresh and then load demo_04_dynamic.json]**

> "Real missions change mid-flight. Targets move, threats appear, drones malfunction. The planner handles this.

> Let me show you a dynamic mission scenario."

**[Click: Solve, then Animate]**

**[Let it run partway, then Click: Edit (while animating)]**

> "I'm now editing mid-mission. Watch — I'll add a new SAM zone here..."

**[Add a SAM zone in the path of a drone]**

> "...and maybe remove this target that's no longer relevant."

**[Remove a target]**

**[Click: End Edit (the Editing button)]**

> "When I accept the edits, notice the **white diamond** — that marks the cut point. Everything before is frozen. The planner re-solves from the drones' current positions."

**[Point out the new solution]**

> "The drones now have new routes that account for the changed environment. I can continue animating..."

**[Animate a bit more]**

> "...and make more cuts if needed. This entire dynamic mission can be replayed and exported."

**[Optional: Show replay if time permits]**

---

## Section 6: Next Steps & Wrap-up (2:00)

**[Return to main view or show a slide if prepared]**

> "What we're working on next:

> **1. Agent-Driven Dynamic Missions**
> Instead of using buttons to edit, we want officers to say things like: 'D1 and D2 abort to base. D3 through D5 continue for maximum points.' The agent handles the cut and replan.

> **2. Multi-Stop Refueling**
> Currently drones can refuel once. We're extending this to 4 refuel stops, including mid-air tanker support.

> **3. Experiential Learning**
> The agent will learn which allocation algorithms work best for different scenarios — clustered targets vs distributed, single airport vs multiple, and so on.

> **4. Refactor & Delivery**
> We're refactoring the codebase for maintainability and preparing delivery to DOW.

> That's the ISR Mission Planner. Questions?"

---

## Backup Talking Points (if questions arise)

**Q: How does it handle many targets?**
> "The exact solver handles up to about 12-15 targets optimally. Beyond that, the optimizer pipeline adds more. This hybrid approach scales well."

**Q: What about real-time data?**
> "The architecture supports it. Drone positions, new targets, threat updates could be fed in, triggering dynamic replanning."

**Q: Why single agent vs multi-agent?**
> "We tested both. The graph-based multi-agent system works, but a single agent with access to all tools produced equivalent results with simpler architecture."

**Q: What LLM powers the agent?**
> "Claude (Anthropic). The agent has structured access to solving tools through a LangGraph workflow."

---

## Demo Checklist

Before presenting:

- [ ] Server running (`./run_planner.sh`)
- [ ] Hard-refresh before each fresh demo import, locally or on Railway: **Cmd+Shift+R** (Mac) / **Ctrl+Shift+R** (Windows/Linux), then load the JSON.
- [ ] Demo JSON files loaded and tested
- [ ] Agent API key configured
- [ ] Browser zoomed appropriately for screen share
- [ ] Backup environment ready in case of issues
