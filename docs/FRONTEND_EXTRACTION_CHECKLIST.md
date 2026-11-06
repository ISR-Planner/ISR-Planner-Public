# Frontend extraction checklist

Created 2026-10-24. Status: **small-extraction pass closed for this iteration**.
Extractions 1–4 and 6–10 are implemented; lifecycle follow-up 5 is complete.
The [closeout](architecture/FRONTEND_EXTRACTION_CLOSEOUT.md) records completion,
retained work and verification limits. Further work follows the
[Remaining Frontend Architecture Plan](architecture/FRONTEND_ARCHITECTURE_PLAN.md),
not an open-ended sequence of additional helper moves. That subsequent architecture
iteration is now [closed](architecture/FRONTEND_ARCHITECTURE_CLOSEOUT.md) for its
selected general UI scope. Mixed trace controls and
memory actions remain in the application. Backend deferrals remain in the
[Phase 6 closeout](architecture/PHASE_6_CLOSEOUT.md).

## Objective and standing constraints

Reduce the responsibilities owned by `webapp/isr.js` through small, independently
reviewable extractions. Success means a coherent UI component can be loaded and
tested without the whole application while its existing callers keep working.
File-size reduction alone is not a completion criterion.

- Preserve labels, CSS, DOM IDs, URLs, keyboard behavior, saved preference keys,
  request payloads, and initialization timing unless a separate change explicitly
  authorizes a behavior adjustment.
- Keep military targeting, domain calculations, synchronization, tactical routing,
  operational import/export, acceptance, solve/replan, and playback workflows
  outside this workstream. Presentation located near those workflows is not
  automatically general UI; inventory its state dependencies before extraction.
- Preserve classic-script/static hosting. A framework, bundler, ES-module
  conversion, visual redesign, or general event bus is separate work.
- Keep compatibility entrypoints while callers require them. Module ownership
  should be explicit; do not replace a lexical global with a window property
  incidentally or monkey-patch existing methods to accommodate a move.
- Shared HTML already has one source: edit `frontend/templates/planner.html`, its
  weather fragment, and the variant manifest, then regenerate shipped pages.
  Repeated markup in generated HTML is expected; do not reconsolidate or edit it
  by hand. Keep weather on its existing page variants.
- Each extraction owns only its listeners/timers. Preserve the distinction between
  listener cleanup and asynchronous shutdown; do not promise cancellation or
  stale-response suppression that does not exist.

## Existing foundations — retain, do not redo

- [x] Confirm extracted storage, HTTP, DOM, tab, and modal helpers:
  `preferences.js`, `http_client.js`, `dom_helpers.js`, `tab_controls.js`, and
  `modal_control.js`. Contracts are in [Frontend Infrastructure](architecture/FRONTEND_INFRASTRUCTURE.md).
- [x] Confirm shared page generation and explicit page capabilities/lifecycle
  startup in [Shared Page Shell](architecture/SHARED_PAGE_SHELL.md).
- [x] Confirm general infrastructure unit/browser commands and the classification
  of legacy source-extraction tests in [JavaScript Test Inventory](JAVASCRIPT_TEST_INVENTORY.md).

These foundation boxes record existing source contracts. The baseline and
post-extraction results are recorded below; they are not hosted CI results.

## Extraction 1 — settings controller and presentation

### Observed boundary and callers

At inventory, the `LLMSettings` object lived in `webapp/isr.js`. Its implementation
now lives in `webapp/llm_settings.js`; the lexical binding and DOM-ready startup
remain in `isr.js`. It owns provider/model presentation, saved selection, local-host
selection, connection testing, modal access, form listeners, and settings
serialization. It is the completed first extraction.

| Relationship | Current dependency / contract to retain |
| --- | --- |
| Storage | `ISRPreferences` instance; keys `isr_llm_provider`, `isr_llm_model_<provider>`, `isr_ollama_host`; cleanup limited to `isr_llm_key_` entries |
| Transport | `ISRHttp` instance; `/api/llm/providers`, `/api/llm/ollama/models?base_url=...`, `/api/llm/test`; existing JSON/status/error behavior |
| Presentation | Settings DOM lookups, provider/model options, status classes/text, `ISRModal`, and debug notification via `appendDebugLine` |
| Production consumers | Agent Send and `CompareUI` call `LLMSettings.getCurrentSettings()`; their workflows/payload construction remain in place |
| Startup | DOMContentLoaded when the document is loading; immediate init otherwise. `init()` loads providers, mounts listeners, and loads saved settings in its current sequence. |
| Test consumers | `shared-shell.spec.mjs` accesses modal/listener methods and substitutes controller methods. `test_agent_provider_request.js` now loads the actual module plus the composition binding from `isr.js`. |

References are named symbols/files rather than brittle source line counts. Repeat
the caller search at implementation time; this is the source inventory as of the
creation date, not a guarantee that later changes introduce no consumers.

### A. Establish the pre-move baseline

- [x] Inspect branch, working tree, and applicable repository instructions; keep
  unrelated edits and Phase 6 deferrals separate.
- [x] Repeat the `LLMSettings` reference search across source, templates, tests,
  and documentation. Inventory externally used methods and mutable fields.
- [x] Run current syntax, generated-page freshness, infrastructure unit/browser,
  and provider-request checks. Record existing failures before changing source.
- [x] Confirm the shared standard/preview fixture serves local assets and mocked
  APIs; require no credentials, private exports, live providers, or operational
  scenarios for this extraction's acceptance checks.

### B. Define and check the module contract before moving code

- [x] Specify the controller factory and dependencies. Implemented destination:
  `webapp/llm_settings.js`, with a browser namespace factory and CommonJS test
  export consistent with the existing infrastructure modules. The implemented
  entrypoint is `ISRLLMSettings.create(options)` / CommonJS `create(options)`.
- [x] Inject document access, storage/HTTP adapters, modal construction, and debug
  notification. Keep application state, planner functions, and weather modules
  outside the dependency list. Access browser storage/fetch lazily as today.
- [x] Preserve the returned controller's methods/fields and dynamic method lookup
  used by listeners/tests; retain an `LLMSettings` binding in the main script for
  existing consumers. Do not duplicate state in an old/new controller pair.
- [x] Define construction as inert: no auto-mount, requests, or independent
  DOM-ready handler in the new module. Startup remains owned in one place.
- [x] Preserve `setupEventListeners()` / `disposeEventListeners()` ownership and
  remount behavior. `init()` is not an idempotent mount API or async shutdown.
- [x] Record async behavior: provider changes currently initiate model discovery
  without a new cancellation/stale-response policy. Preserve it during the move;
  capture improvements separately rather than folding them into extraction.
- [x] Add focused controller checks for behavior not already covered by the
  existing adapters/browser checks. Cover contracts and failures, not source text
  or snapshots that merely reproduce the implementation.

### C. Make one cohesive extraction

- [x] Move the settings object behind the agreed factory without changing provider
  selection, persistence, validation, API serialization, or status presentation.
- [x] Keep `getCurrentSettings()` results unchanged: `null` for incomplete saved
  selection, hosted `{provider, model}`, and Ollama `{provider, model, base_url}`.
  Hosted credentials must not be persisted or added to browser request payloads.
- [x] Keep existing compatibility methods, including `openModal`, `closeModal`,
  and `getModalControl`; do not use this move to remove Phase 4 wrappers.
- [x] Instantiate one controller at the existing composition boundary in `isr.js`
  and preserve its DOM-ready initialization sequence. Verify both ready-state
  branches and avoid duplicate initialization/listener registration.
- [x] Load the new script after its infrastructure dependencies and before
  `isr.js` in the shared template. Preserve all existing script ordering relative
  to Cesium, preview, diagnostics, and capability startup; update changed asset
  version labels and regenerate all three page outputs.
- [x] Adapt `tests/e2e/test_agent_provider_request.js` to load the actual new module
  instead of slicing a removed object. Keep its existing request assertions;
  never copy the old implementation into a test or drop coverage to get a pass.
- [x] Check for duplicate/leftover controller implementations and stale references.
  Leave the Agent Send and comparison workflow implementations untouched.

### D. Acceptance and review gate

- [x] Verify provider/model restoration, stale stored selections, local host/model
  preferences, provider status, missing/unconfigured-provider handling, model
  discovery failures, and connection-test success/error/transport failure.
- [x] Verify save failures keep the modal open and do not report a successful save;
  cleanup removes only legacy credential keys and preserves unrelated storage.
- [x] Verify form/modal mount, repeated setup, disposal, replacement-element
  remount, backdrop interaction, native button keyboard activation, and focus
  behavior on standard and preview pages in Chromium.
- [x] Verify public settings serialization and existing callers with neutral
  fixtures. Diagnostic page receives structural/script-order/startup coverage;
  do not run its operational diagnostic as general UI acceptance coverage.
- [x] Verify module construction is inert and dependencies can be replaced in
  isolated tests without loading the full `isr.js` application.
- [x] Run the relevant checks below. Parse any new untracked JavaScript explicitly:
  the current syntax command enumerates tracked files and may not include it yet.
- [x] Review the scoped diff for changed labels, defaults, event order, network or
  storage semantics, generated noise, private data, and unrelated formatting.
- [x] Update module contracts/caller evidence and this checklist; record test
  results, limitations, and rollback. Keep commit/merge/deployment status separate.

Done when: one settings controller is owned by the new module, startup and
compatibility callers remain explicit, and relevant unit/browser/caller checks
pass. No requirement to reduce the main script to a target line count.

## Verification commands for extraction 1

From the repository root, use Node 22 or later and the locked npm dependencies
plus matching Chromium. Setup is documented in the test inventory; installation
can require network, while the checks themselves use local assets/mocks.

```sh
make check-pages
make check-frontend
npm run test:infra:unit
npm run test:infra:browser
node tests/e2e/test_agent_provider_request.js
```

After a template change, run `make build-pages` before `make check-pages` and
review/commit inputs and generated outputs together. During implementation also
run `node --check` against the actual new module; add any necessary meaningful
controller cases to the existing infrastructure suite. These commands are the
repeatable gate used for this extraction; dated local results are below. Full Python,
live-service, and operational checks are not prerequisites for a settings-only
move unless its scope/dependencies change.

Rollback is the inverse of the extraction: restore the controller/composition,
script URLs/template/generated outputs, and test adapters together. Preserve
preference keys so no data migration or destructive storage reset is needed.

## Candidate inventory at closeout — no further extraction selected

Settings, debug text presentation, voice controls, trace display, chat presentation,
drawer disclosure, shared text escaping, memory-list display, and viewport
preference persistence are completed extractions. The inventory below is retained
as evidence. Remaining mixed integration is explicitly retained/deferred in the
[closeout](architecture/FRONTEND_EXTRACTION_CLOSEOUT.md); it is not an active task
queue for this closed pass.

| Candidate | Boundary to investigate before selecting it |
| --- | --- |
| Debug text presentation (`appendDebugLine`) | Completed as extraction 2 below; existing callers and compatibility entrypoint retained. |
| Voice control wiring (`attachVoiceHandlers`, `attachTtsEngineSelector`, `wireReplyVoice`) | Completed as extraction 3 below; `voice.js` and agent workflows unchanged. |
| Trace presentation/control wiring | Display-only rendering completed as extraction 4. Capture/aggregation and state-changing clear control remain mixed and are deferred. |
| Viewport preference persistence | Completed as extraction 10; coordinate migration, map state/calculations, geolocation and startup precedence remain in the main script. |
| Memory-list display | Completed as extraction 9; load/count/add/delete/clear and panel controls remain in the main script. |
| Shared HTML text escaping | Completed as extraction 8 in the existing DOM utility; global wrapper and all callers retained. |
| Fleet drawer disclosure | Completed as extraction 7; configuration-derived disabled policy and caller code remain in the main script. |
| Chat card/system-message presentation | Completed as extraction 6; message workflows and reply rendering remain in the main script. |
| Map timer lifecycle finding | Fixed in follow-up 5 below: pending size invalidation is canceled before teardown; the four extractions themselves did not change map lifecycle. |

- [x] After settings acceptance, inventory one candidate's callers, state,
  callbacks, timers/listeners, startup, failure behavior, and existing checks.
- [x] Classify it as general UI, mixed, or operational. Defer mixed/operational
  dependencies rather than extending this extraction into domain workflows.
- [x] Select one coherent component and write its contract/acceptance checklist
  before moving source. Reassess priority based on evidence, not adjacency in
  `isr.js` or file size.

## Extraction 2 — debug text presentation

Selected after source review on 2026-10-24. This is general presentation: the
current helper owns no domain state, storage, listeners, or timers. Production
callers are in `isr.js`, `cesium_view.js`, `weather_preview.js`, and
`weather_checkpoints.js`; settings receives an injected debug callback from the
main script. Existing legacy tests mostly supply their own debug stubs. No test
was found slicing the original helper implementation.

Implemented contract: `ISRDebugOutput.create({getOutput, formatTime})` / CommonJS
`create` returns an inert `prepend(message)` writer. Each call lazily resolves the
current element. If absent, it returns `undefined` without formatting the time
or coercing the message. Otherwise it formats the time once, then assigns
`[time] message\n` plus existing `textContent`. Keep literal text, newest-first
ordering, existing content, JavaScript interpolation behavior, exception
propagation, and unlimited retention. Inject the existing Date locale formatting
at the main-script composition boundary; add no default clock or format policy.

- [x] Inventory source callers, test stubs, DOM identifiers, and loading order;
  classify the helper as general presentation and retain all domain callers.
- [x] Record fresh page/syntax/unit/browser/provider-request baseline before moving.
- [x] Add focused contract checks for inert construction, lazy replacement/missing
  elements, literal text/order/coercion, and propagated dependency failures.
- [x] Extract only text presentation into `webapp/debug_output.js`; instantiate
  the writer near the existing DOM helpers, before any top-level code could log.
- [x] Retain the global `appendDebugLine(message)` function and its return contract;
  leave callers, their messages, and diagnostic disclosure policy unchanged.
- [x] Load the module before `isr.js` through the shared template, update changed
  asset labels, regenerate all pages, and verify ordering/freshness.
- [x] Check the real wrapper on standard/preview pages with neutral text,
  replacement/missing output elements, and the current locale-time formatter.
- [x] Run relevant unit/browser/caller checks and explicit new-file syntax;
  compare the move against source and review the scoped diff.
- [x] Update this checklist/contracts with results, limitations, and rollback;
  report commit/merge/deployment separately.

Done when: the writer is independently testable, the existing function remains a
compatibility delegate, and behavior is verified with neutral fixtures. This does
not expand logging, remove sensitive data from callers, cap history, or introduce
log persistence. Rollback restores the helper/composition, module/script URLs,
template/generated pages, and associated tests together.

## Extraction 3 — voice control wiring

Selected after source review on 2026-10-24. General DOM/control presentation only:
`voice.js` retains STT/TTS engines, downloads, audio requests, and fallbacks.
`attachAgentHandlers` calls `attachVoiceHandlers` during existing startup;
Agent Send invokes `wireReplyVoice` for replies. The selector and enabled-toggle
helpers are internally called but their global compatibility functions remain.
The existing provider-request harness supplies its own reply-voice stub. No other
source-extraction test for these four helpers was found.

Contract: `ISRVoiceControls.create({document, getById, getVoice, fetchRequest})`
returns inert helpers with the existing names plus `dispose()` for mounted control
listeners and `disposeReplyVoice(qaBlock)` for one owned reply handler. Voice and
fetch resolution remain lazy. Mounting becomes repeatable per controller; dispose
before replacing mounted controls. Disposal does not cancel speech, stop STT,
reset labels/visibility, cancel requests, or suppress pending model-list results.
Reply wiring keeps its existing overwrite/autoplay behavior; explicit per-reply
cleanup removes only a handler still owned by this controller, with weak ownership
so detached replies are not retained. It is not an application-wide teardown API.

- [x] Inventory callers, state, listeners, async work, engine/DOM dependencies,
  and test adapters; classify only control wiring as general UI.
- [x] Verify fresh baseline and add focused tests using fake engines/neutral text.
- [x] Extract the four helpers, preserving labels, additive dictation/final focus,
  listen-state classes, speak toggle behavior, replay/cancel, engine options,
  endpoint/default/failure behavior, and event ordering.
- [x] Keep global helpers delegating to one inert controller created in `isr.js`;
  preserve existing startup and Agent Send behavior without new listeners there.
- [x] Provide repeatable control mount/disposal/remount and ownership-safe optional
  reply cleanup; document the narrow lifecycle scope and unchanged async behavior.
- [x] Load the module before `isr.js`, update labels, regenerate pages, and verify
  all variants' script order/freshness. Leave `voice.js` unchanged.
- [x] Exercise real page controls on standard/preview with injected fake engines;
  no real microphone, speech playback, model download, or agent request is needed.
- [x] Run relevant unit/browser/caller/syntax checks, review the diff, update
  contracts/checklist, and record limitations and rollback.

Done when: wiring is independently testable with neutral engines, all original
entrypoints and normal one-time startup behavior remain, and listener ownership
is explicit. Rollback restores composition/helpers, module/script labels,
template/generated pages, and associated tests together. No persistence/data
migration is involved.

## Extraction 4 — trace display rendering only

Selected after boundary review on 2026-10-24. `renderAgentsTraceTab` currently
reads `state.agentTraceRuns` and formats HTML with `escapeHtml` and locale time.
The actual rendering is general presentation; capture/state ownership and clear
control are mixed integration and remain in the main script. Known render callers
are the existing Agent Send response handler, comparison response handler, and
`attachTraceHandlers` clear action. Their state writes, event aggregation,
confirmation, callback order, and messages remain unchanged.

Contract: `ISRTraceView.create({getContainer, escapeHtml, formatTime})` / CommonJS
`create` returns an inert `render(runs)` view. Each render lazily resolves the
container and reads supplied records; it never owns or mutates them. The main
script injects existing lookup/HTML escaping and
`ts => new Date(ts).toLocaleTimeString()`. Preserve empty text, newest-first run
order, event order, truncation, optional ID/data display, error/warn/info classes,
escaping, and current malformed-input/error propagation. Add no validation,
retention, storage, network, event subscriptions, or asynchronous work.

- [x] Inventory render callers, trace writers, clear handler, dependencies and
  existing checks; select only the display boundary.
- [x] Record fresh baseline and add focused checks with neutral records before
  moving the renderer; no operational fixtures or agent requests are required.
- [x] Extract rendering into `webapp/trace_view.js`; retain `renderAgentsTraceTab`
  as a delegate supplying current state, plus the existing shared `escapeHtml`.
- [x] Keep trace capture, comparison aggregation, state replacement/clearing,
  confirmation, logging, and all operational workflows unchanged in `isr.js`.
- [x] Load the inert module before the main script through the shared template;
  update changed asset labels, regenerate all pages, verify loading/freshness.
- [x] Verify neutral display on standard/preview in Chromium, including actual
  escaping and current clear-confirmation behavior without changing that handler.
- [x] Run unit/browser/caller/syntax checks; compare source preservation outside
  the renderer/composition span and review the scoped diff.
- [x] Update contracts, checklist, results/limitations and rollback. Mixed trace
  controls are deferred rather than silently included in the move.

Done when: display is independently testable from supplied neutral records and
existing state/caller ownership remains explicit. No listener/timer lifecycle is
needed for this inert renderer. Rollback restores renderer/composition, script
labels/template/generated pages, and tests/docs together; no stored-data migration.

## Extraction 1 validation and status (2026-10-24)

Pre-move baseline: 40 unit checks and 24 Chromium checks passed, along with
page freshness, tracked syntax, and the existing provider-request check. After
extraction: **50 unit checks and 26 Chromium checks passed**, plus the unchanged
provider-request assertions. The new module was parsed explicitly in addition
to the tracked-file syntax command (25 tracked scripts at this point).

Ten new isolated checks exercise inert/independent construction, async init order,
saved-selection serialization, failed/successful saves, missing/unconfigured
selection, local discovery errors/restoration, and the actual application
composition under both document ready states. Two new Chromium cases exercise
local-model discovery failure and refresh recovery on standard/preview pages.
Existing listener/modal/keyboard/storage and error checks also pass. All three
pages have module loading/order/freshness checks; diagnostic operational workflows
were not run. No live providers, credentials, or private exports were needed.

Source comparison verified that `isr.js` outside the settings composition span
is unchanged, controller methods retain their original logic after dependency
substitution, and template-string contents are preserved. The Agent Send and
comparison workflow implementations were not changed. A source search found no
remaining embedded settings object or test harness expecting its old declaration.
Generated page edits add the script and update the main-script version label;
labels, IDs, values, capability flags, and styles remain unchanged.

Frontend/test syntax, page freshness, documentation links, and diff whitespace
checks passed. Module contracts and relevant documentation are updated. There
is no new full Python, hosted CI, live-service, or deployment result. Broader
asynchronous cancellation/stale-response behavior is unchanged and remains a
separate improvement if selected later. Rollback is described above. Nothing
was committed, merged, or deployed.

## Extraction 2 validation and status (2026-10-24)

Fresh baseline: 50 unit / 26 Chromium checks passed, along with provider-request,
syntax, and page freshness checks. After extraction: **53 unit / 28 Chromium
checks passed**, plus the existing provider-request check. The two new browser
cases exercise the real compatibility wrapper on standard/preview pages with
neutral strings, a controlled locale-time formatter, and replacement/missing
output elements. The three new unit checks cover inert construction, lookup/time
ordering, literal/newest-first text, interpolation, and exception propagation.

`debug_output.js` owns no listeners, timers, browser globals, storage, or network.
The main script injects lazy `$` lookup and the unchanged
`new Date().toLocaleTimeString()` expression. Module loading/order/freshness is
verified on all three page variants. Diagnostic operational execution was not
needed. Browser and unit checks use no live providers or private exports.

Source comparison verified that main-script changes consist only of the prior
settings extraction and the debug writer composition/delegate. Caller message
construction and all domain/calculation/workflow code are unchanged. Retention,
formatting, disclosure policy, and error propagation have not been redesigned.
Tracked frontend syntax plus explicit syntax for both currently untracked modules,
test syntax, documentation links, and diff checks passed. No full Python, hosted
CI, live-service, commit, merge, or deployment result is claimed. At this step voice and trace remained unselected. Voice was subsequently
reviewed/extracted as recorded below; trace remains mixed and unselected.

## Extraction 3 validation and status (2026-10-24)

Fresh baseline: 53 unit / 28 Chromium checks, provider-request assertions, syntax,
and page freshness passed. After extraction: **61 unit / 30 Chromium checks
passed**, along with provider-request assertions and freshness/syntax checks.
Eight new isolated checks use fake engines for inert/missing-engine behavior,
additive dictation and final focus, listen-state labels/classes, speak toggles,
engine options/defaults/failure behavior, cleanup/replacement remount, reply
ownership/autoplay/replay, and unchanged pending-response behavior after disposal.
Two new Chromium checks exercise standard/preview controls with a replaced voice
script and mocked engine list; no microphone, playback, download, provider call,
or agent request is used by those checks.

The four global helpers remain delegates to one `voiceControls` instance in
`isr.js`. Construction is inert; existing startup still calls the mount helper.
Normal one-time behavior and labels are preserved. Repeated control mounting now
avoids duplicate listeners and discovery calls; disposal/remount and optional
per-reply cleanup are explicit additions. Disposal does not silence/stop engines,
undo UI labels, cancel requests, or suppress pending results. Reply ownership is
weak, and cleanup cannot remove a handler another consumer has installed.

The shared template loads the module before the main script; all three generated
pages have loading/order/freshness coverage. Source comparison verified that only
the three reviewed UI composition/helper spans changed in `isr.js`; `voice.js`,
Agent Send, comparison code, and domain/workflow logic remain unchanged. Explicit
syntax checks cover all three currently untracked modules and the new tests.
Documentation links and diff checks passed. No full Python, real-engine/live
service, hosted CI, commit, merge, or deployment is claimed. Rollback is described
in extraction 3 above. At this step trace was unselected. Subsequent review selected only its inert
display renderer; state-changing trace integration remains deferred.

## Extraction 4 validation and status (2026-10-24)

Fresh baseline: 61 unit / 30 Chromium checks passed, plus provider-request,
syntax, and page freshness. After extraction: 65 unit checks passed. Two new
standard/preview Chromium cases verify neutral trace rendering, actual HTML
escaping, locale formatting, ordering, optional data, and the existing clear
confirmation's cancel/accept behavior using native keyboard activation. They
submit no agent requests. All generated variants have loading/order coverage.

The renderer's logic is unchanged after substituting its record and presentation
dependencies. It owns no state, retention, listeners, timers, storage, or network.
Source comparison confirmed that `isr.js` outside the four reviewed UI composition/
helper spans is unchanged, including `attachTraceHandlers`, Agent Send capture,
and comparison event aggregation. The shared `escapeHtml` helper remains in the
main script for existing callers. Operational trace interpretation is not added.

An initial full Chromium run had 31 passes and one failure in the existing
weather/3D lifecycle check: `Cannot read properties of undefined (reading
'_leaflet_pos')`. Its captured stack points to the unchanged
`setTimeout(() => map.invalidateSize(), 200)` in `initLeafletMap`. The failing case
then passed three isolated repetitions. This records a timing-sensitive map
lifecycle finding, not a trace-rendering assertion failure. It was not suppressed,
retried automatically, or fixed as part of this extraction. A separate focused
map lifecycle review should cover delayed work after destroy/reopen. A subsequent
full Chromium run passed all 32 checks (21.6 seconds).

Unit/caller checks, tracked syntax plus explicit syntax of all four new modules,
new test syntax, page freshness, source preservation, documentation links, and
diff checks passed. No full Python, real-provider, hosted CI, operational-scenario,
commit, merge, or deployment result is claimed. Rollback is described in extraction
4 above. Mixed trace controls are deliberately retained; no remaining candidate
is implicitly selected for a broader workflow rewrite.

## Follow-up 5 — Leaflet delayed resize lifecycle

Selected as a focused general UI lifecycle fix after extraction 4's recorded
browser finding. This is not a map/domain extraction. `initLeafletMap` schedules
one 200 ms size invalidation per new map; `destroyLeafletMap` must cancel that
pending work before removing the map. A stale callback must not invalidate a
removed or replacement map. Preserve the live map's resize timing, map options,
view synchronization, tile selection, and existing callers.

- [x] Reproduce the removed-map callback with deterministic browser time on
  standard/preview using real local Leaflet and neutral map fixtures.
- [x] Give the pending resize explicit ownership; cancel/reset it on teardown,
  clear it after firing, and guard against a stale map instance.
- [x] Verify repeated initialization/teardown, destroy before 200 ms, immediate
  replacement, and exactly one resize at 200 ms for the live map.
- [x] Run relevant browser, unit, syntax, page freshness, and caller checks;
  review source scope, document results, and keep deployment status separate.

Rollback restores the timer scheduling/teardown, main-script asset label and
regenerated pages together, plus the regression test and this status. No data or
preference migration is involved.


### Follow-up 5 validation and status (2026-10-24)

Before the fix, both new deterministic Chromium cases failed at the original
200 ms callback with the same `_leaflet_pos` error recorded in extraction 4.
After the fix, the full infrastructure suite passed: **65 unit / 34 Chromium
checks**, including the existing 3D lifecycle cases, with retries disabled.
Provider-request assertions, frontend/new-test syntax, generated-page freshness,
and diff checks passed. A launch attempt inside the sandbox could not start
Chromium; the regression reproduction and passing run used the approved local
browser execution environment.

The current-step source diff changes only the timer field, scheduling callback,
and cancellation at teardown. There is no map extraction or change to map options,
view calculations, synchronization, or operational workflows. The main-script
asset label and all generated pages were refreshed. No full Python, live-provider,
operational-scenario, hosted CI, commit, merge, or deployment result is claimed.
The earlier observed timer finding is resolved by this focused follow-up; mixed
trace controls remain deferred. Inventory another general UI boundary before
selecting any further extraction.

## Extraction 6 — chat card and system-message presentation

Selected after a fresh candidate inventory. `createQABlock(question)` builds a
detached card; its only production caller is Agent Send. `appendChatMessage` and
`removeChatMessage` are retained legacy system-message helpers with no current
production call sites found. Existing provider-request tests stub `createQABlock`;
keep that caller harness unchanged. Memory management has inline delete actions
and transport/state effects; comparison/GNN views contain domain interpretation.
Those candidates remain unselected.

Contract: `ISRChatView.create({document, getHistory, escapeHtml, formatTime,
createMessageId})` returns inert `createQABlock`, `appendChatMessage`, and
`removeChatMessage` helpers. Construction does no DOM work. Card creation returns
a detached element, escapes question text, preserves existing labels/markup and
the hidden voice button, and uses the existing hour/minute locale format. System
messages preserve their existing caller-supplied HTML contract (no new sanitization
or rendering policy), append order, scrolling, return ID, and null return when
history is absent. IDs retain the existing Date/Math expression at composition.
Removal preserves lazy document lookup, no-op for missing/falsy IDs, and current
exception propagation. No state, transport, listeners, timers, or startup is owned
by the view. Leave shared `escapeHtml` and all caller logic in the main script.

- [x] Record fresh unit/browser/provider-request/syntax/page baseline.
- [x] Add isolated contract checks and neutral standard/preview browser checks.
- [x] Extract only the three presentation helpers and compose one inert view;
  keep global delegates, existing caller behavior, and all workflows unchanged.
- [x] Load the module before `isr.js` through the shared template, refresh changed
  labels, regenerate all variants, and verify order/freshness.
- [x] Verify detached cards, literal escaped questions, labels/hidden replay button,
  legacy system HTML/order/scrolling, returned IDs, missing/replacement history,
  removal/no-op behavior, and dependency failures without agent requests.
- [x] Run relevant checks, compare source scope, update documentation/results and
  report commit/merge/deployment separately.

Rollback restores the three helpers/composition, script/template/generated-page
changes, tests, and docs together. No data or preference migration is involved.


### Extraction 6 validation and status (2026-10-24)

Fresh baseline: **65 unit / 34 Chromium checks passed**, with provider-request,
page freshness, and tracked syntax checks. After extraction: **69 unit / 36
Chromium checks passed**, retries disabled. Four isolated contracts and two new
standard/preview checks cover the view with neutral content. The first browser
run had 34 passes and two new-test failures caused by assuming the existing
welcome message was absent; correcting the assertions to preserve it produced
the full passing run. No production behavior was changed to satisfy those tests.

Source comparison against the pre-step file confirms only the three helper
implementations and inert view composition changed in `isr.js`. Module logic
matches the original after the documented dependency substitutions, preserving
template literal contents. Agent Send, response formatting, memory management,
comparison/GNN code, map lifecycle, voice playback, and all operational workflows
are unchanged. Global helpers and shared HTML escaping remain available.

Provider-request assertions, tracked syntax (29 scripts), explicit new-module/test
syntax, generated-page freshness, documentation links, and diff checks passed.
The shared template loads the view once before the main script on all variants;
changed asset labels are `20261024-chat-view`. No full Python, live-provider,
operational-scenario, hosted CI, commit, merge, or deployment result is claimed.
Rollback is described above. Memory management and domain-dependent comparison/GNN
views remain unselected; mixed trace controls remain deferred.

## Extraction 7 — fleet drawer disclosure controls

Selected after caller/dependency inventory. Only `attachFleetDrawers` and
`setDroneDrawer` are general DOM disclosure behavior. `attachConfigListeners`
mounts the listeners once at existing startup; `updateFleetRowStates` closes a row
when configuration disables it. Those callers and their configuration/disabled
policy remain unchanged. No external source-extraction tests for the helpers were
found. Nearby configuration, table detail, target-hover and map controls have
state/calculation dependencies and remain unselected.

Contract: `ISRFleetDrawers.create({document, getById})` returns inert
`attachFleetDrawers()`, `setDroneDrawer(did, open)`, and `dispose()`.
The first helper mounts only drawer-toggle/collapse listeners, once per mount.
Disposal removes only owned listeners, is repeatable, and preserves displayed
state. Dispose before replacing controls, then remount to capture replacements;
mounting with missing controls is safe and requires disposal before remount.
Direct `setDroneDrawer` always resolves current nodes, including after disposal.
Preserve independent open rows, `hidden`, `aria-expanded`, arrow labels, row
`open` class, collapse visibility, the existing five-row collapse range, missing
node behavior, and propagated errors. Native disabled-button behavior remains;
add no configuration or enabled-state dependency. No timers, network, storage,
application-wide teardown, or independent startup is introduced.

- [x] Record fresh unit/browser/caller/syntax/page baseline before the move.
- [x] Add focused contract tests for inert construction, disclosure presentation,
  listener ownership/repeated mount/disposal, replacement remount, and missing
  controls; add standard/preview native keyboard/disabled-control coverage.
- [x] Move only the two helpers, retain global delegates and existing callers,
  and compose one inert controller without changing configuration/workflow code.
- [x] Load the module before the main script through the shared template, refresh
  changed labels, regenerate pages, and verify loading/order/freshness.
- [x] Verify live DOM/ARIA/arrow/class/collapse behavior, multiple open rows,
  repeated mount/disposal, replacement controls and lazy direct updates.
- [x] Run relevant checks; compare current-step source scope and update contracts,
  results, limitations, and rollback. Report deployment status separately.

Rollback restores helpers/composition, module/template/generated pages, tests,
and documentation together. No storage or data migration is involved. Repeatable
mount/disposal is a narrow controller lifecycle addition, not a remount contract
for `attachConfigListeners` or the full application.


### Extraction 7 validation and status (2026-10-24)

Fresh baseline: **69 unit / 36 Chromium checks passed**, plus provider-request,
tracked syntax, and generated-page freshness. After extraction: **74 unit / 38
Chromium checks passed**, retries disabled. Five isolated contracts and two
standard/preview browser cases verify disclosure and ownership with neutral DOM
fixtures. The first browser run had 36 passes and two new-test timeouts because
the assertions attempted pointer clicks inside the existing edit-locked section.
The tests now use native keyboard activation without changing the edit gate,
configuration, or production code. The subsequent full run passed in 27.3 seconds.
No broader edit-gate or disabled-policy redesign is claimed by these checks.

Current-step source comparison confirms only the two helpers and their inert
composition changed in `isr.js`; the drawer setter's presentation logic is
unchanged. Listener registration now has explicit once-per-mount ownership and
repeatable disposal/remount. `attachConfigListeners`, `updateFleetRowStates`,
configuration writes, disabled-row policy, all calculations, and workflows are
unchanged. The lifecycle addition applies only to these disclosure listeners.

Provider-request assertions, tracked frontend syntax (29 scripts), explicit new
module/test syntax, generated-page freshness, documentation links, and diff checks
passed. The shared template loads the new module once before `isr.js` on all three
variants; changed asset labels use `20261024-fleet-drawers`. No full Python,
live-provider, operational-scenario, hosted CI, commit, merge, or deployment result
is claimed. Rollback is described above. Mixed trace controls remain deferred;
other domain-dependent candidates remain unselected.

## Extraction 8 — shared HTML text escaping

Selected as an extension of the existing DOM utility boundary rather than a new
module. `escapeHtml(text)` uses a detached div's `textContent`/`innerHTML`; it owns
no application state, startup, listeners, storage, or requests. Main-script
presentation callers and the injected chat/trace adapters rely on its global
entrypoint. Keep every caller and injected adapter unchanged. No legacy test was
found slicing this helper implementation; the provider-request harness stubs it.
Memory management, comparison/GNN rendering, import/export, and other domain-
dependent candidates remain unselected.

Contract: add `escapeHtml(text)` to `ISRDom.create(document)`'s returned interface.
Each call creates one detached div, assigns its supplied value verbatim to
`textContent`, and returns `innerHTML`. Preserve the browser's existing conversion,
nullish, character-escaping and failure behavior. This is HTML text serialization,
not a new sanitizer, attribute encoder, or policy for existing HTML-bearing callers.
Do not replace it with regex escaping or add quote escaping/string conversion.
Retain global `escapeHtml(text)` delegating through the existing `domHelpers`
instance. Construction stays inert; no new script or startup path is needed.

- [x] Record fresh unit/browser/provider-request/syntax/page baseline.
- [x] Add isolated DOM delegation/failure contracts and standard/preview browser
  checks for real escaping, nullish/primitive/custom conversion, quote behavior,
  detached nodes and failure propagation without requests or domain fixtures.
- [x] Move only this helper into `dom_helpers.js`; retain the compatibility wrapper,
  existing composition/startup, injected adapters, and every caller unchanged.
- [x] Refresh both changed script labels in the shared template, regenerate all
  variants, and verify existing loading order/page freshness.
- [x] Run relevant checks; compare source scope, update documentation/results and
  report commit/merge/deployment separately.

Rollback restores the helper/wrapper, DOM utility interface, asset labels and
regenerated pages, plus tests/docs together. No preference or data migration is
involved. Existing HTML contexts and encoding limitations are preserved.


### Extraction 8 validation and status (2026-10-24)

Fresh baseline: **74 unit / 38 Chromium checks passed**, plus provider-request,
tracked syntax, and generated-page freshness. After extraction: **76 unit / 40
Chromium checks passed**, retries disabled (36.5 seconds for the browser suite).
Two isolated contracts and two standard/preview real-DOM cases cover the shared
utility and global delegate. Existing chat/trace caller checks also pass. No
production behavior or encoding policy was changed to satisfy these checks.

Current-step source comparison confirms the main-script change is only the
`escapeHtml` compatibility delegate, and the DOM module change is only the exact
helper implementation plus its exported method. All callers, their contexts,
chat/trace adapters, startup, configuration and operational workflows are unchanged.
This preserves existing HTML-text serialization; it does not claim comprehensive
attribute escaping, sanitization, or an audit of all HTML-bearing callers.

Provider-request assertions, tracked frontend syntax (29 scripts), new/changed
test syntax, generated-page freshness, documentation links, and diff checks passed.
The shared template refreshes `dom_helpers.js` and `isr.js` labels to
`20261024-html-text` on all three generated variants. No new script or startup path
was introduced. No full Python, live-provider, operational-scenario, hosted CI,
commit, merge, or deployment result is claimed. Rollback is described above.
Mixed trace controls remain deferred; domain-dependent candidates remain unselected.

## Extraction 9 — memory list display only

Selected after reviewing the remaining mixed memory UI. Only `renderMemoryList`
is selected: it reads supplied records and writes list markup. Its sole direct
caller is `loadAgentMemories`; memory loading, count updates, add/delete/clear,
confirmation, input handling, panel visibility, requests, logging and startup
remain in `isr.js`. The renderer emits an existing inline `deleteAgentMemory(id)`
button action; that global entrypoint must remain unchanged. No legacy test was
found slicing the renderer. Comparison/GNN, map and trajectory controls, operational
import/export and other state-dependent candidates remain unselected.

Contract: `ISRMemoryView.create({getList, escapeHtml})` / CommonJS `create` returns
an inert `render(memories)`. Resolve the list on each render; absent list is a
no-op before reading records. Preserve empty text/style, record order, IDs,
category markup, content escaping, delete markup/action, input ownership, and
current malformed-input/error behavior. Preserve existing interpolation of IDs
and categories; this move does not change input validation or HTML policy. It adds
no state, listeners, subscriptions, transport, timer, or startup. Keep global
`renderMemoryList(memories)` delegating through one main-script view instance.

- [x] Record fresh unit/browser/provider-request/syntax/page baseline.
- [x] Add focused renderer contracts for inert/lazy/missing/replacement nodes,
  empty display, ordering/content escaping/input ownership, and failures.
- [x] Extract only the renderer, preserve template literal contents and the global
  delegate, and verify all memory transport/state/action code stays unchanged.
- [x] Load the view before `isr.js` through the shared template, refresh changed
  asset labels, regenerate all variants, and verify order/freshness.
- [x] Verify standard/preview empty/populated display, actual escaping, existing
  delete-button ID dispatch using a temporary neutral callback, replacement nodes,
  and unchanged panel-toggle behavior without memory writes or agent requests.
- [x] Run relevant checks; review source scope, update contracts/results/limitations,
  and report commit/merge/deployment separately.

Rollback restores renderer/composition, template/script/generated-page changes,
plus tests/docs together. No storage/data migration is involved. Memory actions
remain mixed integration rather than part of this display-only extraction.


### Extraction 9 validation and status (2026-10-24)

Fresh baseline: **76 unit / 40 Chromium checks passed**, plus provider-request,
tracked syntax and generated-page freshness. After extraction: **80 unit / 42
Chromium checks passed**, retries disabled (32.0 seconds for the browser suite).
Four isolated contracts and two standard/preview browser cases cover the renderer
with neutral records. The existing inline delete dispatch is exercised via a
temporary callback recorder and restored, with no memory writes or agent requests.

Current-step source comparison confirms the only main-script changes are the
renderer delegate and inert composition. Module logic matches the original after
renaming the method and replacing the list lookup; template literal contents are
preserved. Memory loading/counts/actions/toggling/startup, shared escaping,
configuration, calculations, and all operational workflows are unchanged.

Provider-request assertions, tracked frontend syntax (29 scripts), explicit new
module/test syntax, generated-page freshness, documentation links and diff checks
passed. The shared template loads the new module once before the main script on
all three variants; changed labels use `20261024-memory-view`. No full Python,
live-provider, operational-scenario, hosted CI, commit, merge or deployment result
is claimed. Rollback is described above. Memory actions and trace controls remain
mixed integration; domain-dependent candidates remain unselected.

## Extraction 10 — viewport preference persistence

Selected after inventory of `saveViewToLocalStorage`, `readSavedView`, storage
constants, their callers, and the map-coordinate test harness. This boundary
owns only the stored last-view record and duplicate-write key. Map state,
projection/constraining, coordinate migration, saved-view application, geolocation,
startup precedence, import/export and all callers remain in the main script.
`tests/e2e/test_map_coordinates.js` slices these wrappers and must load the actual
new module/composition rather than retain a copied persistence implementation.

Contract: `ISRViewPreferences.create({getStorage, now, storageKey, ttlMs,
defaultZoom, normalizeView})` returns inert `save(view)` and `read()` methods.
Keep `isrLastView`, its four JSON fields, 30-day TTL, default zoom 13, the existing
five/five/one-decimal duplicate key, and setting that key *before* attempting a
write (failed writes of the same rounded view are not retried). Serialization
stores the supplied unrounded values. Key formatting happens outside the storage
try/catch; storage/serialization/clock failures during writes are swallowed.
Reads preserve absent/malformed/type/timestamp checks, strict `age > ttl` expiry,
nullish zoom fallback, and normalization inside the catch boundary. Do not add
finite/range validation, alter future-timestamp behavior, reset deduplication on
read, or add storage rollback/retry. Inject existing coordinate migration as an
opaque callback; its implementation stays unchanged in `isr.js`.

The main script keeps both global wrappers and existing constants. Compose once
after those constants, with lazy storage/clock/normalization adapters. Do not read
`viewState` during construction. Preserve call timing and startup precedence.

- [x] Record fresh unit/browser/caller/syntax/page baseline.
- [x] Add isolated storage contracts for lazy/inert construction, serialization,
  rounding/deduplication/failure retry behavior, validation/expiry/defaults,
  normalization/error boundaries, and independent controller instances.
- [x] Extract only persistence and its duplicate key; preserve global wrappers,
  main-script constants, migration, calculations, startup and every caller.
- [x] Adapt the existing source-slicing caller harness to load the actual module
  and production composition, retaining all existing assertions.
- [x] Load the module before `isr.js` through the shared template, refresh changed
  labels, regenerate all variants and verify order/freshness.
- [x] Verify standard/preview wrapper save/read/deduplication, reload restoration,
  storage-failure behavior and unrelated preference preservation with neutral views.
- [x] Run relevant checks, compare source scope, update docs/results/limitations
  and report commit/merge/deployment separately.

Rollback restores helper implementations/duplicate key/composition, module and
script labels/template/generated pages, test adapter and docs together. Keep the
existing stored record; no data migration or destructive reset is needed.


### Extraction 10 validation and status (2026-10-24)

Fresh baseline: **80 unit / 42 Chromium checks passed**, plus provider-request,
12 coordinate caller regressions, tracked syntax and generated-page freshness.
After extraction: **86 unit / 44 Chromium checks passed**, retries disabled
(30.7 seconds for the final browser run), plus provider-request and all 12 existing
coordinate regressions. The first browser run had 43 passes and one new preview
assertion failure due to tiny existing map-constraining floating-point differences
after reload. The corrected assertion uses coordinate tolerance; direct writes
still assert exact unrounded stored values. Production rounding was not changed.

Six new unit contracts and two standard/preview browser cases use neutral views
and verify persistence/read behavior, deduplication, write denial/recovery, reload,
and unrelated preferences. The fixture now seeds a neutral view only when absent
so it does not overwrite the persisted record on reload. Existing browser checks
continue to pass. The coordinate harness loads the actual module/composition;
none of its twelve assertions or existing workflow implementations were changed.

Current-step source comparison confirms only persistence, its duplicate key, the
two wrapper bodies and inert composition changed in `isr.js`. Module logic matches
the original after explicit dependency substitutions. Coordinate migration,
projection, map state/application, saved-view precedence, geolocation, imports/
exports, calculations and operational workflows remain unchanged. No new retry,
validation, cleanup, or migration policy was introduced.

Tracked frontend syntax (29 scripts), explicit new-module/test syntax, generated
page freshness, documentation links and diff checks passed. Changed script labels
use `20261024-view-preferences` on all three generated variants. No full Python,
live-provider, operational-scenario, hosted CI, commit, merge or deployment result
is claimed. Rollback is described above. Mixed trace controls and memory actions
remain retained; domain-dependent candidates remain unselected.


## Pass closeout and architecture handoff (2026-10-24)

The user selected closure of the small-extraction pass and planning for the
remaining frontend. All selected implementation acceptance items above are
complete. The [closeout](architecture/FRONTEND_EXTRACTION_CLOSEOUT.md) records the
nine extractions, lifecycle follow-up, retained integration and explicit deferrals.
The [architecture plan](architecture/FRONTEND_ARCHITECTURE_PLAN.md) inventories
remaining responsibilities/state/startup and stages a general UI composition
boundary before further source moves. Its implementation has not started.

The latest recorded implementation verification remains 86 unit / 44 Chromium
checks, provider-request assertions and 12 coordinate regressions. This handoff is
documentation-only: local link/consistency/diff checks are separate from those
historical implementation results. No source, runtime behavior, stored data or
generated HTML changes are included in the handoff. No commit, merge or deployment
was performed by this documentation step.


Architecture follow-up (2026-10-26): [stages A/B general UI composition](architecture/FRONTEND_UI_COMPOSITION.md)
are now implemented and locally verified under the architecture plan. This does
not reopen the small-extraction pass or change its historical results above.
