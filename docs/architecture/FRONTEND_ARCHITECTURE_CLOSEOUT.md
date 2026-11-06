# Frontend architecture iteration closeout

Status: **stages A–E complete; closed for this iteration**, 2026-10-26.
This closes the selected general UI scope in the
[Frontend Architecture Plan](FRONTEND_ARCHITECTURE_PLAN.md). It does not declare
all of `isr.js` refactored. The earlier
[small-extraction pass](FRONTEND_EXTRACTION_CLOSEOUT.md) remains separately closed.

## Evaluation against the selected goals

| Goal | Result and evidence | Practical limit |
| --- | --- | --- |
| Explicit general UI construction | `frontend_ui.js` constructs the same seven components once, in order, using named factories and lazy adapters; existing lexical bindings receive those exact instances | Settings and viewport composition remain at their timing-sensitive locations; no universal startup function |
| Clear listener ownership | Existing tab/modal/settings/voice/reply/drawer methods are inventoried and tested for their narrow cleanup/remount contracts | Cleanup is not async shutdown, engine cancellation, or application-wide remount support |
| Explicit presentation inputs | Trace/memory use borrowed supplied records; debug/chat keep documented text/HTML/formatting contracts | Existing HTML policies, inline actions and application-owned writers remain |
| Independent verification | Real modules compose against an isolated document with neutral inputs; forbidden engine/transport callbacks remain unused; host UI is untouched | This is not live-service, real-engine or operational-scenario verification |
| Compatibility and startup preservation | Existing entrypoints, object identities, initialization phases, preference keys, script/page capabilities and weather placement are retained | Compatibility wrappers and shared state remain intentional integration surfaces |
| Deliberate remaining scope | Responsibility/state/startup maps and retained-work dispositions identify what did not move | The inventory is not proof of single-writer ownership for the remaining application state |

The selected boundary now has construction, lifecycle and presentation contracts
that can be reviewed and tested separately. That is the architecture gain. C/D
needed characterization and documentation, not additional runtime abstractions.
No runtime speed, load-time, defect-rate or broad state-decoupling improvement is
claimed from this iteration.

## Stage completion

| Stage | Completed deliverable |
| --- | --- |
| A. Characterize composition | Caller/dependency contract and tests for order, identity, inert construction, late lookup and lexical bindings |
| B. Introduce composition | One inert general UI construction factory; existing entrypoints and startup retained |
| C. Lifecycle ownership | Existing narrow interfaces documented; unrelated-listener, replacement and pending-work behavior characterized |
| D. Presentation adapters | Supplied-value/callback and snapshot contracts documented; mutable-record and isolated real-DOM evidence added |
| E. Evaluate and close | This evaluation, current boundary map, explicit retained/deferred scope, validation limits and handoff |

Contracts and rollback evidence:
[Composition](FRONTEND_UI_COMPOSITION.md),
[Lifecycle](FRONTEND_UI_LIFECYCLE.md),
[Presentation adapters](FRONTEND_PRESENTATION_ADAPTERS.md).

## Final boundary and source-scope audit

Current dependency direction is shared page factories → inert composition factory
→ the main script's construction call and retained lexical instances. Existing
application callers supply display values/callbacks to views. Lifecycle ownership
stays with individual controls; `frontend_ui.js` does not add mount/init/dispose.
The plan's responsibility, state and startup maps remain the reference inventory.

Review against architecture baseline `c58b7f3` found the runtime delta limited to:

- New `webapp/frontend_ui.js` construction factory.
- The adjacent top-of-file construction block in `webapp/isr.js`.
- Its shared-template script registration/version labels and three generated pages.

The main script outside that construction block is unchanged against that baseline.
No existing component implementation, wrapper, settings/viewport construction,
DOM-ready/load branch, state/workflow code or backend changed in the selected
architecture iteration. C/D added only tests and documentation; E adds documentation
and performs source/consistency validation. Existing stage D working-tree changes
are preserved, not committed or discarded by closeout.

At evaluation, `isr.js` still has 17,649 lines / 740,863 bytes. The small size change
from the initial architecture snapshot is not an acceptance measure or performance
result. Shared state and application coordination still account for substantial
remaining responsibility.

## Retained and deferred work

| Area | Disposition at closeout | Required condition before future work |
| --- | --- | --- |
| Settings/viewport composition and multiple startup phases | Retain current construction/init timing | A separate timing/caller contract; no automatic consolidation into the general UI bundle |
| General UI disposal versus pending async work | Retain existing limits documented in stage C | A separate async-lifecycle proposal before cancellation, stale-response suppression or terminal shutdown changes |
| Trace capture/aggregation/clear and memory loading/count/actions/panel controls | Retain existing main-script integration | A cohesive state/action/caller review before moving mixed responsibilities |
| Existing HTML/text policies, inline actions and compatibility entrypoints | Preserve current contracts | A separately scoped policy/caller migration; this pass is not a comprehensive HTML-security audit |
| Application/workflow state and segment-related objects | Retain current identities and writers | Dedicated ownership analysis; a replacement store or merge is not selected |
| Map calculations/interaction, configuration, editing and domain-dependent presentation | Retain outside the selected general UI boundary | Separate dependency classification and scoped work; no adjacency-driven extraction |
| Domain calculations, routing, solve/replan, synchronization, acceptance, import/export and playback | Outside this architecture iteration; unchanged | Separate scoped work; no implementation redesign is supplied by this closeout |
| Framework/bundler/ES-module or directory conversion | Not required or selected | Independent justification and compatibility/deployment plan |
| Backend/infrastructure deferrals | Remain in [Phase 6 closeout](PHASE_6_CLOSEOUT.md) | Their existing individual conditions; frontend closure does not resolve them |

No further extraction or new stage is selected automatically. Use the established
general UI boundaries for ordinary maintenance; select a new architecture task
from a concrete maintenance problem with its own scope, contracts and evidence.
Stable mixed sections may remain in place while ownership is unresolved.

## Verification and limits

Recorded implementation/characterization results:

| Point | Infrastructure unit | Chromium | Qualification |
| --- | --- | --- | --- |
| Architecture baseline | 86 | 44 | Fresh A/B baseline |
| A/B complete | 92 | 46 | New composition plus characterization |
| C complete | 97 | 48 | Tests/docs; runtime unchanged |
| D complete / latest recorded | **99** | **50** | Tests/docs; runtime unchanged; browser retries disabled |

Provider-request assertions passed in the relevant baselines; stage reports record
syntax, page freshness, source preservation and limits. The 12 coordinate
regressions were last recorded during viewport extraction, before this architecture
iteration; they were not rerun or added to the current 99/50 counts. Viewport and
coordinate implementations remain unchanged in the source-scope audit.

Stage E is documentation-only. Its new checks are source-scope/unchanged-main-body
audit, local documentation links/status consistency, 34 tracked frontend syntax
checks, freshness of all three generated pages and diff checks. It does not rerun
or relabel the prior 99/50 results as fresh stage E test execution. Earlier initial
failures/corrections remain documented in their original records.

No full Python, live-provider, real-engine, operational-scenario or hosted CI result
is claimed. Commit, merge and deployment are separate outcomes; none was performed
by this closeout.

## Exit criteria

- [x] Selected construction, ownership and presentation boundaries are explicit.
- [x] Compatibility/startup evidence and independent neutral checks are recorded.
- [x] Runtime scope is audited against the architecture baseline.
- [x] Remaining mixed/core work has an explicit retained/deferred disposition.
- [x] Current versus historical validation and local versus external status are distinct.
- [x] Plan/navigation reflect closure; no further runtime work is implicitly selected.
