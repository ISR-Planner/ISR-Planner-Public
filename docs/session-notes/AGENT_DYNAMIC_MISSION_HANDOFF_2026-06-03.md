# Handoff — Agent in the Dynamic Mission (Phase 0)

**Date:** 2026-06-03
**Branch:** `feat/optimizer-pipeline`
**For:** continuing the "Agent-driven dynamic mission + per-mission memory" work at home.

> **How to use this file:** Read it top to bottom. To resume with Claude, paste the
> "Prompt to resume" block at the very bottom, then say which task to start. The full
> living spec is in `docs/ISR_PLANNER_DESCRIPTION.md` — this handoff is the short version.

---

## 1. The goal (what we're building)

Give the **Agent** the ability to re-plan a mission *while it is animating*, the same
way the **Editor** can today — and back it with **per-mission memory** so the agent
behaves like a real human helper (recall, suggestions, contradiction-flagging).

Three layered features, in dependency order:

```
§1a-0  The Mission        — a first-class object: name/number + explicit start/end + owns the environment
          │ enables
          ▼
§1e    Constraint ledger  — durable, structured conditions the agent reasons against (catches "never land at A4" vs "land at A4")
          │ powers
          ▼
§1c/§1d Agent SEND        — SEND in the Agent tab == Editor DONE: halt anim, apply condition, re-solve un-frozen part, accept, continue
```

---

## 2. Agreed scope / decisions (don't re-litigate these)

- **Run Planner** = unconditional solve. **Agent Solve** = conditional solve
  ("visit Tx,Ty first", "last target must be priority 10", "don't end at A5").
- **Editor owns geometry; Agent owns conditions.** Geometry edits are Editor-only.
  A single **SEND** can resolve a staged edit **+** a condition together.
- **Agent SEND behaves exactly like Editor DONE:** halt animation → freeze flown
  part (cut point `Cx`) → apply condition → re-solve un-frozen remainder → Accept →
  continue animating.
- **Behavior rules:** condition is mandatory; if infeasible → best-effort solution
  **+ explanation** (user accepts/rejects); a 0-fuel drone → **red diamond + notification**.
- **The Mission** ends only by an **explicit officer action** (not by editing).
  Edits stay **inside** the same mission. The **Mission owns the environment**.
- **Cut points:** white diamond = normal cut; **red diamond** = drone de-selected
  at that checkpoint (`lostDrones`). Created by `freezeAtCheckpoint()` in `webapp/isr.js`.
- **Persistence — DECIDED:** build on the `session_storage.py` model
  (`sessions`/`conversations`/`mission_plans`), activate the dormant
  `server/memory/session_store.py`; keep `mission_ledger.py` for per-run traces.

---

## 3. Key code facts (already traced — trust these)

- **The Agent is BLOCKED during animation today.** `sendAgentMessage()` in
  `webapp/isr.js` (~12717) returns early on `perms.isAnimating`. Enabling Agent SEND
  begins by relaxing this guard.
- **The cut/freeze machinery exists:** `acceptEditsWithCheckpoint()` in `webapp/isr.js`
  (~2283) — the Editor DONE handler. SEND should reuse it. It has **no condition channel** yet.
- **Constraints are parsed per-turn and discarded:** `parse_constraints(user_message,…)`
  in `server/agents/isr_agent_multi_v4.py` (~3693). The §1e ledger = "persist instead of discard".
- **Existing infra (big head start):** `server/memory/constraints.py` (ConstraintProgram +
  dataclasses + compiler), `constraint_parser.py`, `mission_ledger.py` (Supabase tables incl.
  `agent_runs.hard_constraints`).
- **Gaps for §1e:** no "never land at Ax" constraint type; no cross-turn accumulation +
  contradiction check; ledger not wired as the agent's working memory.

---

## 4. Phase 0 progress (Make the Mission a first-class object)

| Task | Status | Notes |
|---|---|---|
| **0.1** Extend data model | ✅ DONE | `MissionRecord` got `name`/`status`/`created_at`/`ended_at` + `is_active`/`end()`. Backward-compatible. |
| **0.2** Centralize mission-id minting | ✅ DONE | `new_mission_id()` + `get_or_create_mission()` in `main.py`. Behavior unchanged. Tests green. |
| **0.3** Stop implicit forking on edit | 📋 PLANNED, NOT STARTED | **The next task. Highest risk.** See §5. |
| 0.4 Mission owns env + durable persist | ⏳ scoped | Construct real `MissionRecord`; persist via `session_store.py`. |
| 0.5 Lifecycle endpoints | ⏳ scoped | `POST /api/missions`, `POST /api/missions/{id}/end`, `GET /api/missions/{id}`. |
| 0.6 Frontend declare/end mission | ⏳ scoped | Name UI + End Mission; audit `missionId=null` resets. |

### Code changed this session (uncommitted, in working tree)
- `server/agents/mission_store.py` — `MissionRecord` extended + `STATUS_ACTIVE/ENDED`, `end()`.
- `server/main.py` — `new_mission_id()` + `get_or_create_mission()`; all 6 mint sites routed through them.

### Migration written (NOT applied to any DB)
- `docs/architecture/migrations/001_sessions_to_missions.sql` — adds `name`, `status`,
  `ended_at`, `env`, `drone_configs`, `env_hash`, `cfg_hash` + status CHECK + indexes to `sessions`.

---

## 5. THE NEXT STEP — Task 0.3 (read before resuming)

**Critical finding from the pre-0.3 investigation: fork-on-edit is THREE mechanisms,
not one. 0.3 changes #1 while preserving the *effect* of #2 and #3.**

1. **Identity fork (the 0.3 target — SAFE).** `get_or_create_mission` mints a new id on
   mismatch. The `continuity` flag it returns is **never read by the frontend** (0 reads),
   so keeping the id is safe.
2. **Solution-staleness guard (MUST PRESERVE).** `agent_chat_v4` (`main.py` ~2110–2121)
   sets `mission_id=None` on mismatch *to avoid loading stale routes*, not to fork identity.
   Keep the "solve fresh" effect but stop nulling the id. (Identity vs. solution-validity
   are conflated in one `None` sentinel — **separate them**.)
3. **Frontend `invalidateMission()` (the real coupling).** `isr.js` (~6807–6883) nulls
   `state.missionId` on **9 drone-config edits**. This *actively* forks from the frontend
   and conflicts with §1a-0. **Precedent to reuse:** it already preserves frozen
   trajectories during checkpoint workflow (~4276–4282) — generalize that.

**Revised 0.3 sub-plan:**
- (a) Backend: in `get_or_create_mission`, if stored mission is `STATUS_ACTIVE`, keep its
  id + record a new attempt instead of minting.
- (b) Backend: split staleness from identity so #2 still solves fresh without nulling the id.
- (c) Frontend: make `invalidateMission()` invalidate the *solution* (routes/mode) **without**
  nulling `state.missionId` while a mission is active.

---

## 6. Open questions / to verify at home

- **Is the Supabase schema actually deployed?** The `.sql` proves design intent, not that
  it was run against prod. Verify via DB access (`\dt`). Until then `InMemorySessionStore`
  keeps everything working without a DB.
- Freeze-line rule (exact frame vs. nearest waypoint).
- Condition persistence across multiple SENDs (likely resolved by the §1e ledger).
- End-Mission UX (button/command; what happens to in-flight animation on End).

---

## 7. Prompt to resume with Claude (paste this)

```
Read docs/ISR_PLANNER_DESCRIPTION.md (the living spec) and
docs/session-notes/AGENT_DYNAMIC_MISSION_HANDOFF_2026-06-03.md (the handoff).
We are building "Agent in the Dynamic Mission" + per-mission memory.
Phase 0 tasks 0.1 and 0.2 are DONE (uncommitted in the working tree).
The next task is 0.3 — stop implicit mission forking on edit — which has a
3-part sub-plan in §5 of the handoff (backend keep-id, backend split-staleness,
frontend invalidate-solution-not-id). Confirm you understand the 3-mechanism
finding, then implement 0.3 part (a) first with verification.
```
