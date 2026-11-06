# Agent-Driven Segmented Missions — Continue From Cut

**Status:** Working end-to-end, browser-verified. Committed `df109c0` on
`feat/or-opt-optimizer` (2026-07-12).

## What it does

Lets the operator continue a mission mid-flight through the agent:

1. Solve a mission, Accept, Animate.
2. Pause mid-flight.
3. (Optional) Edit the environment — add/move targets, SAMs, tankers.
4. Tell the agent to **continue** (or give a mid-mission order).
5. The agent **cuts** at each drone's current position and **replans forward**
   from there — carrying remaining fuel, excluding already-visited targets,
   honoring the order.
6. The proposal renders as a checkpoint replan: frozen flown path + white cut
   diamond + drone sitting AT the cut. The **Accept** button arms.
7. **Accept** commits the segment and animates from the diamond.
   **Discard/Reject** removes the proposal and re-opens the editor with the
   staged edits intact — no solve, no state change.

The manual flow is unchanged: turning the editor OFF still triggers the
automatic solver (`acceptEditsWithCheckpoint`). Only the agent path replans
via the agent instead of the auto-solver.

## Supported orders

The agent translates natural language into solver constraints, so arbitrarily
complex commands work at a cut:

| Order | Solver constraint |
|---|---|
| "continue" / "keep going" / "solve from here" | plain solve from the cut |
| "visit T3, then T7, then T9" | `before=[[T3,T7],[T7,T9]]` |
| "T9 first" | `first=T9` |
| "T9 last" | `last=T9` |
| "keep T4 and T5 together" | `adjacent=[[T4,T5]]` |
| "D1 to A5" | D1 `end_airport=A5` |
| "abort, all land at A5" | every drone ends at A5, no targets |
| "D3 is down" | disable D3 |
| "only visit T3, T5" | restrict the remaining target set |

## Design principle — the LLM never decides "is this a continue?"

The earlier approach used a dedicated `continue_from_pause` tool and let the LLM
choose it. The LLM could instead pick `solve_full_mission`, which re-solves from
the airport and **loses the cut**. That was the root of a whole class of bugs
(no diamond, drone jumps to the airport, editor stays on).

The rebuild removes that failure mode entirely:

- **Deterministic routing.** The frontend owns the animation state, so it
  *knows* the mission is paused. When `canAgentContinue` is true, a Send routes
  to `agentContinueFromCut(...)` — the LLM is never asked to classify the intent.
- **The cut lives in the environment.** The frontend sends the same checkpoint
  env the manual solve uses, where each drone's `start_airport` is the cut node
  (`C{n}-{d}` / `D{did}_START`). Because the start *is* the cut, **any** solve the
  agent runs continues from the cut. Whatever tool the LLM picks, it cannot lose
  the cut — there is no airport start to fall back to.
- **One render path.** Agent routes flow into the same
  `applyDraftSolutionToUI` a manual C-key cut uses (checkpoint draft: frozen
  prefix + diamond + Accept). No `route[0]` sniffing, no agent-special branch.

## Code map

### Frontend (`webapp/isr.js`)

- **`prepareCutFromCurrentState()`** — the shared cut engine, extracted from
  `acceptEditsWithCheckpoint`. Freezes each drone's flown prefix, mints the
  `C{n}-{d}` ids (single cut-number source via the `_cutCounterClaimed` guard —
  no leak), carries remaining fuel/refuels, applies the staged env, merges
  `initialEnvSnapshot` so newly-added targets get the green ✕, and turns the
  editor OFF. Returns `{ checkpointSegNum, cutPositions, frozenPrefixes,
  cutDistance }`. Does **not** solve.
- **`acceptEditsWithCheckpoint()`** — manual live-edit Done: `prepareCut` +
  `runPlanner` (auto-solve) + `acceptSolution`. Behavior unchanged.
- **`agentContinueFromCut(message, qaBlock)`** — `prepareCut` →
  `buildCheckpointEnv()` (the identical cut env) → POST `/api/agents/reason`
  with `is_continue_from_cut: true` → feed routes into a checkpoint draft.
- **`applyAgentContinuation(routes, cut)`** — builds the checkpoint draft
  (`isCheckpointReplan: true` + frozen prefixes + cut positions) and pushes it
  through the shared `applyDraftSolutionToUI`.
- **Send handler** (`sendAgentMessage`) — routes to `agentContinueFromCut` when
  `canAgentContinue`.
- **Reject** (`discardDraftSolution` top branch) — an `_agentContinuation` draft
  re-opens the editor with the staged edits restored (`agentRejectRestore`), no
  solve.
- **`canAgentContinue`** (getUiPermissions) — true when
  `PAUSED_MID_ANIMATION`, or `EDITING_ENV && wasAnimating` (live-editing from a
  pause). These are the only mid-mission states where the agent box is enabled.

### Backend

- **`server/agents/reasoning_agent.py`** — the `continue_from_pause` tool and all
  `pause_context` plumbing are **removed**. `run_reasoning_agent` takes
  `is_continue_from_cut`; when true it folds a `[CONTINUATION]` directive into
  the user turn to keep provider message ordering compatible while mapping the
  order to solver constraints. The agent then solves the cut env with its normal
  tools.
- **`server/main.py`** — `ReasoningAgentRequest.is_continue_from_cut` and
  `ReasoningAgentResponse.solution_kind` (`"continuation"`). The UI reads the tag
  rather than sniffing `route[0]`.

## The "drone goes back through the cut" bug (root fix)

**Symptom:** on a second cut, the drone flew *back* to a target it had already
visited (T4/T5/T3), through the cut, before continuing.

**Root:** `buildCheckpointEnv()` has three branches. Two of them (the
segmented-import and live-creation paths) filter already-visited targets out of
`env.targets`. The **checkpoint branch** only recorded them as
`env.visited_targets` *metadata* and left them in `env.targets`.

- The **manual** solve survived this because the server
  (`/api/solve_with_allocation`) re-filters `visited_targets`.
- The **agent** continue fed the unfiltered target list to the Insert-Missed
  optimizer. That optimizer computes `unvisited = env.targets − targets-already-
  in-the-route` and does **not** read `env.visited_targets`. So a target
  completed in a prior segment looked "missed" and got **re-inserted** — sending
  the drone back to it.

**Fix:** make the checkpoint branch filter visited targets out of `env.targets`,
consistent with its two sibling branches. The optimizer is untouched — it
correctly trusts `env.targets` to be the pending set. Verified via a direct API
repro: excluding the visited target yields `C1-1 → T5 → T3 → …` (no backtrack)
versus `C1-1 → T4 → …` (backtrack) when unfiltered.

This is a symptom of the broader manual-vs-agent solve-path asymmetry (see the
solve-path-duplication note): two solve entry points where a filtering rule was
honored on one path and not the other.

## Testing

**Before importing:** It is advisable to hard refresh before every fresh mission/environment import or **Load demo**, on both localhost and Railway. In Chrome, use **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux). This reloads the latest scripts and starts a fresh page; then import the file to initialize the mission and help prevent stale state from a previous run carrying over.

1. Hard refresh, then load an env, Run Planner, Accept, Animate.
2. Pause before the first target. Add a target. Say "continue".
3. Expect: diamond at the cut, frozen flown path, drone AT the cut, editor OFF,
   Accept armed. Accept → animates forward from the diamond.
4. Animate again, pause after some targets are green-✕. Say "continue".
5. Expect: the new route does **not** return to any green-✕ (visited) target.

Debug lines to watch:
- `buildCheckpointEnv: … targets=[…]` — visited ids must be **absent**.
- `✅ Agent continuation applied … kind=continuation`.
