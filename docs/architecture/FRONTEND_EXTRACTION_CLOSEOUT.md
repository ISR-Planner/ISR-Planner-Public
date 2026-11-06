# Frontend small-extraction pass closeout

Status: **closed for this iteration**, 2026-10-24. This closes the selected work
in the [Frontend Extraction Checklist](../FRONTEND_EXTRACTION_CHECKLIST.md), not
all frontend refactoring. The next workstream is defined in the
[Remaining Frontend Architecture Plan](FRONTEND_ARCHITECTURE_PLAN.md).

## Completed scope

Nine extractions and one lifecycle follow-up are implemented:

| Checklist item | Result | Retained integration |
| --- | --- | --- |
| 1. Settings controller/presentation | `llm_settings.js` | Lexical `LLMSettings`, startup and request consumers |
| 2. Debug text | `debug_output.js` | `appendDebugLine` and caller messages |
| 3. Voice controls | `voice_controls.js` | Existing entrypoints, `voice.js` engines and reply callers |
| 4. Trace display | `trace_view.js` | Trace capture, aggregation, state and clearing |
| 5. Leaflet lifecycle follow-up | Pending resize canceled before teardown | Map integration and calculations |
| 6. Chat presentation | `chat_view.js` | Sending, reply handling and existing HTML policy |
| 7. Drawer disclosure | `fleet_drawers.js` | Configuration-derived disabling and startup |
| 8. HTML text escaping | Existing `dom_helpers.js` extended | `escapeHtml` wrapper and all caller contexts |
| 9. Memory-list display | `memory_view.js` | Loading, count, actions and inline delete entrypoint |
| 10. Viewport preferences | `view_preferences.js` | Migration, map state/application and startup precedence |

The checklist contains the contracts, caller evidence, per-item rollback, test
results and corrected test assumptions. Its completed acceptance boxes remain
historical evidence. No further small helper extraction is selected by this closeout.

Classic scripts, existing entrypoints, preference keys, page capabilities and
weather placement are retained. Shared HTML is already generated from one template;
repeated shipped HTML is not an outstanding source-duplication task.

## Explicit retained work and deferrals

| Area | Disposition | Condition for revisiting |
| --- | --- | --- |
| Trace capture/aggregation/clear | Retain in `isr.js`; display extraction is complete | A separate state/action contract and caller review |
| Memory loading/count/add/delete/clear/panel control | Retain in `isr.js`; display extraction is complete | A cohesive controller proposal preserving current request/action semantics |
| Map state, projection, migration, interaction and startup | Retain; only preference persistence and resize cleanup were selected | A separate boundary review; no implied calculation or interaction rewrite |
| Category/legend preferences | Retain existing keys and fallback rules | Document the individual contracts before considering consolidation |
| Shared mutable application/workflow state and segment-related objects | Retain current identities and writers | Dedicated ownership analysis; no replacement store is authorized by this closeout |
| Domain calculations, routing, solve/replan, synchronization, import/export, acceptance and playback | Outside this general UI pass; unchanged | Separate scoped work, not automatic follow-on extraction |
| Comparison/GNN controllers and domain-dependent presentation | Retain mixed integration | Separate dependency classification; adjacency to UI is insufficient |
| Global compatibility wrappers and inline actions | Retain while callers exist | Complete source/template/test/external-caller evidence before removal |
| Full application teardown, remount and async cancellation | Not provided by the extracted controllers' narrow cleanup methods | Explicit lifecycle design distinguishing listeners, timers and pending async work |
| Backend/infrastructure deferrals | Remain in [Phase 6 closeout](PHASE_6_CLOSEOUT.md) | Their existing individual conditions; frontend closeout does not resolve them |

## Verification and limits

Latest recorded implementation verification, from extraction 10:

- **86 infrastructure unit checks and 44 Chromium checks passed**, browser retries
  disabled. Browser checks use local assets and mocked APIs.
- Provider-request assertions and **12 existing coordinate regressions passed**.
- Frontend/new-file syntax, all three generated-page freshness checks,
  documentation links and diff checks passed.

These results belong to the completed implementation pass. The closeout and
architecture plan are documentation-only changes; they do not claim a new browser
run, live-provider verification, full Python/operational suite or hosted CI result.
Known initial test failures and corrections remain documented in the extraction
checklist; they are not relabeled as passing initial runs.

At this review, `webapp/isr.js` has 17,665 lines / 741,121 bytes at repository
revision `c58b7f3`. This is an inventory snapshot, not a size target or a comparison
to historical metrics in older architecture proposals. File size alone does not
establish a runtime performance problem.

No commit, merge or deployment is performed by this documentation closeout.
Changes already present in repository history are not newly attributed to this step.

## Exit and handoff

- [x] Selected checklist items have recorded completion and verification.
- [x] Mixed integration and outside-scope responsibilities have explicit dispositions.
- [x] Existing compatibility, startup, page generation and storage contracts remain.
- [x] Remaining responsibilities, ownership risks and an incremental next plan are documented.
- [x] Implementation verification is distinguished from documentation-only validation.

The next candidate is a **general UI composition boundary** under the architecture
plan. Its implementation is not started here. Additional work should be selected
from that plan rather than appending item 11 to the closed small-extraction pass.
