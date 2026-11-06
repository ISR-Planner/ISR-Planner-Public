# ISR Mission Planner — Demo Speaker Script

**Audience:** USAF TPOC + Kraken team
**Runtime:** ~10 minutes
**Env file:** `demo/isr_env_DEMO_compact.json`

**Before each fresh import:** Hard-refresh with **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux), then load the mission JSON. Use this sequence on both the local server and Railway, including when switching demos. It reloads the latest scripts and starts a fresh page so the import initializes without carrying over the previous run's in-memory state.

> **One-line thesis:** Same problem Kraken solves — optimal ISR mission planning.
> Our edge: the operator states intent in plain English, the planner returns
> **provably optimal** routes under real threats and fuel limits, and it
> **replans live** when the mission changes.

---

## Open — the positioning · `0:00–0:30`

**SAY:**
> "Kraken and we are solving the same mission — how do you route ISR assets
> optimally. I want to show you three things that set ours apart: you **talk**
> to it, it gives you routes that are **provably** good — not a heuristic guess —
> and when the mission changes mid-flight, it **replans on the spot**."

---

## Beat 1 — Natural-language tasking · `0:30–3:00`
*The differentiator.*

**DO:**

- Hard-refresh, then load `isr_env_DEMO_compact.json`. Point out the airport, the tanker, the six targets, and the SAM threat ring.
- **Type:** `Solve the mission.` → an optimal route appears, bending around the SAM.
- **Type:** `Start by visiting T2, then T5, before completing the mission.` → the route re-plans and honors the order.

**SAY:**
> "No forms, no parameters, no dropdowns. The operator states **intent** — in the
> words they'd use over the radio — and the agent translates it into a plan."
>
> "Notice it routed **around** the SAM automatically, and when I asked for a visit
> order, it honored it exactly while still optimizing everything else."

---

## Beat 2 — Provable optimality + auto-refuel · `3:30–6:00`
*The proof.*

**DO:**
- Drop drone D1's fuel budget to ~35 so one tank can't cover the mission.
- **Type:** `Visit all the targets.` → the drone refuels at the tanker **en route**, then continues — it does not fly home to refuel.

**SAY:**
> "It decided on its own **whether, when, and where** to refuel — it took the
> tanker that was already on the way, not a wasteful trip back to base."
>
> "And this isn't a heuristic that stops at 'good enough.' We verify it against an
> exhaustive optimal search. On our benchmark, the naïve plan was 303 kilometers;
> this one is 233 — the same coverage, a third less distance."

*(If unsure the 303→233 number matches this exact env, soften to "roughly a third less distance.")*

---

## Beat 3 — Honors the command, or tells you why not · `6:00–7:30`
*Operator trust.*

**DO:**
- With fuel still tight, give a constraint it can't satisfy.
- **Type:** `Visit T5 first.` (a target it can't reach first within one tank) → the agent **reports** it can't, and asks whether to allow a refuel or drop the constraint.

**SAY:**
> "This is the part operators care about most. It **never fakes success**. It
> either satisfies the command exactly, or it tells you plainly why it can't —
> and asks how you want to proceed. That's what earns trust in the field."

---

## Beat 4 — Mid-mission replan · `7:30–9:00`
*Resilience.*

**DO:**

- Hard-refresh, then import the pre-built segmented mission. Hit **Animate** — the drones fly, reach a checkpoint, replan from their current positions, and continue.

**SAY:**
> "Missions don't stay static. A drone drops out, a new threat pops up, a target
> moves. You cut the mission at the aircraft's **current position**, re-solve from
> there, and keep flying — this is not a plan-once tool."

> **Note:** On the local server and Railway, use **Cmd+Shift+R** (Mac) or
> **Ctrl+Shift+R** (Windows/Linux) before importing this new scenario. Segment
> replay is verified working on the deployed URL.

---

## Close — the roadmap hook · `9:00–10:00`

**SAY:**
> "And it **learns**. Every mission — the intent, the plan we produced, and whether
> the operator accepted it or corrected it — is captured. Over hundreds of missions
> it recognizes recurring situations and reuses what worked."
>
> "So the planner doesn't just optimize a single sortie — it gets **smarter with
> every mission it flies**. Happy to dig into any of these."

---

## Pre-flight checklist

- [ ] Server running / Railway available.
- [ ] Hard-refresh before each fresh mission import, locally or on Railway: **Cmd+Shift+R** (Mac) / **Ctrl+Shift+R** (Windows/Linux), then load the JSON.
- [ ] `isr_env_DEMO_compact.json` loads + solves cleanly (dry-run once).
- [ ] Segmented mission file on hand for Beat 4.
- [ ] Know the two fuel numbers: **80** (Beat 1, no refuel) → **~35** (Beats 2–3, forces refuel).
- [ ] Have the one stat ready: **303 → 233 km**, same coverage.
- [ ] Fallback if the agent stalls: re-send the command once, or switch to the local server.

---

## Known gotchas (don't get surprised live)

- **Drone `speed` field:** leave it at **1**. Setting it high wrongly inflates fuel
  (it multiplies loiter fuel). With loiter = 0 it's harmless, but don't touch it live.
- **Segmented env *creation*** can falsely say "route exceeds fuel budget" even with
  a tanker adjacent — so for Beat 4 use a **pre-built** segmented file, don't create
  one live.
- **Railway** deploys from `main`; it's synced with all current fixes as of 2026-07-09.
