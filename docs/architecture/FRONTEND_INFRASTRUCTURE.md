# Frontend Infrastructure Boundaries

The [Frontend Extraction Checklist](../FRONTEND_EXTRACTION_CHECKLIST.md) records
the completed small-extraction contracts and dated verification. Both that
[pass](FRONTEND_EXTRACTION_CLOSEOUT.md) and the subsequent
[general UI architecture iteration](FRONTEND_ARCHITECTURE_CLOSEOUT.md) are closed
for their selected scope. The [architecture plan](FRONTEND_ARCHITECTURE_PLAN.md)
remains the responsibility/state/startup map and stage history. The contracts
below describe implemented behavior; closure does not select further runtime work.

The first Phase 4 extraction isolates string-preference persistence from the
settings UI. `webapp/preferences.js` has no dependency on DOM elements, network
services, or application state. Settings presentation initially remained in `webapp/isr.js`; the later controller
extraction is recorded below.
The second extraction moves its three JSON requests into `webapp/http_client.js`.

## Initial dependency map

| Area | Dependencies and callers | Decision |
| --- | --- | --- |
| Settings persistence | Browser Storage; settings load/save and provider/model/host getters | First extraction: lazy storage adapter with explicit failure results |
| Settings UI | Provider API, modal DOM, debug output, saved preferences | Extracted to `llm_settings.js`; retain the existing `LLMSettings` entrypoint and validation rules |
| Other local storage | Viewport and category/legend preferences have their own keys and fallback rules | Viewport persistence extracted as `view_preferences.js`; category/legend preferences remain unchanged |
| API transport | Direct `fetch` calls throughout settings and other UI; differing status/error handling and cancellation behavior | Settings-only JSON adapter extracted; other callers retain their existing semantics |
| DOM utilities | `$`, `setText`, and rendering helpers called throughout the main script | Generic ID lookup, text assignment, and HTML text serialization extracted; global wrappers retained; specialized rendering unchanged |
| Reusable controls | Tab switching uses `.tab-btn`/`.tab-pane`; modal listeners attach during settings initialization | Tabs and modal visibility extracted with explicit mount/dispose; settings form actions remain in the controller |

## Shared DOM helper contract and caller inventory

`webapp/dom_helpers.js` exports `ISRDom.create(document)` in browsers and `create`
through CommonJS. Construction performs no lookups. `getById(id)` forwards the ID
to `document.getElementById`, returning its element or null without interpreting
the ID as a CSS selector. Every call performs a fresh lookup, so replaced DOM
nodes are not cached. `setText(id, value)` writes `textContent`: null/undefined
clear the text; other values use `String(value)`. A missing element is a no-op
and does not convert the value. Existing lookup/conversion errors propagate.
Both functions preserve their original return behavior. No listeners, timers,
HTML parsing, application state, or disposal are involved.

The source inventory found `$()` throughout `isr.js` and four calls in
`cesium_view.js`; `setText()` callers are in the main script's statistics/list
presentation. The environment-tab source-extraction test supplies its own
`setText` stub. Keep the global `$()` and `setText()` wrappers: they delegate to
the new module while all existing callers and presentation calculations remain
unchanged. Tests exercise only generic DOM operations with neutral elements.
Specialized rendering helpers are outside this extraction.

Extraction 8 extends the same interface with `escapeHtml(text)`. Each call creates
one detached div, assigns the value verbatim to `textContent`, and returns
`innerHTML`; native conversion and DOM failures propagate. It preserves nullish
clearing, text escaping, unchanged quotes and Unicode, and custom conversion.
This is HTML text serialization, not an attribute encoder or sanitizer. It adds
no parsing, caching, listeners, startup, or new script. Existing HTML-bearing
callers and encoding policy are unchanged.

The global `escapeHtml` remains a delegate through the existing `domHelpers`
instance. Main-script rendering callers and the chat/trace injected adapters
remain unchanged. Tests use neutral values and real DOM serialization on standard
and preview pages; they do not exercise operational scenarios.

## Preference adapter contract

Browser entrypoint: `ISRPreferences.create(getStorage)`.
Node tests import the same file through CommonJS. No bundler or new production
dependency is required.

`getStorage` is a caller-supplied function returning a standard Storage-compatible
object. It is called lazily; accessing `window.localStorage` can itself throw.

| Method | Contract |
| --- | --- |
| `read(key, fallback = null)` | Return a stored string verbatim, including an empty string. Return the fallback for missing/non-string values or storage-access errors. No parsing or semantic validation is performed. |
| `write(entries)` | Accept pairs of non-empty string keys and string values. Reject malformed input before writing. Return `true` on success and `false` on failure. Snapshot previous values before writing and attempt restoration after a partial write failure. |
| `removeMatching(prefix)` | Remove only keys beginning with a non-empty string prefix. Snapshot matching keys before deleting to avoid skipping entries during enumeration. Return success/failure explicitly. |

Storage writes are not transactional. Restoration after a failure is best effort
because browser policy or quota errors may also prevent rollback. The adapter
does not silently switch to in-memory persistence or report a failed save as
successful. It registers no listeners or timers and needs no disposal method.

## JSON request adapter contract

Browser entrypoint: `ISRHttp.create(fetchRequest)`; Node tests use CommonJS.
`json(url, options)` forwards the URL and the same options object to the injected
request function, then returns the decoded JSON body. Construction performs no
request. The settings controller resolves browser `fetch` at request time.

The adapter deliberately decodes non-2xx responses too: the existing settings UI
uses the response body's `success` and `message` fields to present errors.
Network, cancellation, and JSON decoding failures propagate unchanged to the
caller's existing catch handler. It adds no retries, timeouts, headers, credentials,
or body serialization. Caller-provided abort signals pass through unchanged.
It owns no listeners or timers and requires no disposal.

Only provider listing, local model listing, and connection testing use this
adapter. Their payload construction and UI status handling remain in
`LLMSettings`; other request callers are outside this extraction.

## Tab control contract

`webapp/tab_controls.js` exports `ISRTabs.create(document)` in the browser and
`create` through CommonJS. Construction is inert. `mount()` captures the current
`.tab-btn` and `.tab-pane` elements and installs click listeners; repeated mounting
on the same controller is a no-op. `dispose()` removes those exact listeners,
releases the captured references, and may be called repeatedly. Remounting reads
the DOM again. The caller owns one controller per document and must dispose it
before replacing the tab markup.

A click clears the active class from the captured buttons/panes, activates the
clicked button, and activates `tab-<data-tab>` if it exists. A missing pane leaves
all panes inactive, matching the previous implementation. Mounting and disposal
do not reset selection or mutate markup. Native button Enter/Space activation,
focus, labels, and CSS remain unchanged; there are no new keyboard shortcuts.

`attachTabSwitching()` remains the startup compatibility entrypoint in `isr.js`
and owns a single controller. It delegates to `mount()` without accumulating
listeners. The page currently keeps this controller for its full lifetime;
future shell teardown can call its `dispose()` method. Other DOM utilities and
the settings form's action listeners remain outside this extraction.

## Modal visibility contract

`webapp/modal_control.js` exports `ISRModal.create(options)` (CommonJS `create`).
The options supply lazy `getModal`, `getOpenButton`, and `getCloseButton` getters
plus an optional `onOpen` callback. Construction performs no DOM access.
`open()` sets the modal display to `flex`, then invokes `onOpen`; `close()` sets
it to `none`. Missing elements are ignored, and no open callback runs without a
modal. These methods also work before mounting or after disposal.

`mount()` installs button and backdrop click handlers once. Only a click directly
on the backdrop closes the modal; child interactions remain inside it. `dispose()`
removes the captured handlers without hiding the modal. Both are repeatable;
remounting reads the current elements. The owner must dispose before replacing
markup. No Escape handler, focus trap, or focus restoration was added; native
button keyboard activation remains unchanged.

`LLMSettings.getModalControl()` owns the controller, while `openModal()` and
`closeModal()` remain compatibility entrypoints. Provider-status refresh is an
injected open callback. Form change, save, refresh, and connection-test handlers
remain in `LLMSettings.setupEventListeners()`; that method is now a
repeatable event-mount API. It records each form element, event type, and handler
and skips duplicate registration. `disposeEventListeners()` removes those exact
handlers plus modal handlers, releases the element references, and is repeatable.
Dispose before replacing form markup, then call `setupEventListeners()` to bind
the replacement elements. Refresh still prevents the button's default action.

This is listener cleanup, not full asynchronous controller shutdown. It does not
cancel requests, suppress pending response callbacks, reset preferences, or hide
the modal. `init()` still loads provider data and saved settings; use the event
setup method for remounting rather than repeating initialization.

## Preserved settings behavior

The UI still owns the existing keys and defaults:

- `isr_llm_provider`
- `isr_llm_model_<provider>`
- `isr_ollama_host`
- Legacy credential cleanup prefix `isr_llm_key_`

The adapter neither knows these names nor builds API request payloads. Provider
and model availability checks, the local-provider host default, and
`LLMSettings.getCurrentSettings()` remain in the existing settings controller.
Other code continues to use that entrypoint without an interface change.

Unknown saved provider/model strings are handled by the existing dropdown logic.
The new behavior for unavailable storage is explicit: reads use their existing
fallbacks, and a failed save leaves the modal open with a storage-error message.
It does not update the current selection fields or emit the successful-save log.
Legacy credential cleanup is best effort when storage is inaccessible; no
credential values are included in the preference request serialization.

## Loading and compatibility

All three shipped HTML entrypoints load `/static/preferences.js` and
`/static/http_client.js`, plus `/static/tab_controls.js`, `/static/modal_control.js`,
and `/static/dom_helpers.js`, before
`/static/isr.js`: standard, weather preview, and weather diagnostic pages.
The main-script version label is advanced for the DOM helper extraction.

The older provider-request test evaluates the settings object in a VM. Its
adapter now loads the storage, HTTP, and modal modules before evaluating that object; its
request assertions and operational fixtures were not changed. Other legacy
source-extraction tests remain outside this extraction's validation scope.

## Verification

`make test-infrastructure` now runs Node unit checks followed by the offline
Chromium suite. Local results for this extraction:

- Twenty-five unit checks passed, including four DOM lookup/text contracts,
  three modal lifecycle checks,
  four tab lifecycle/selection checks
  using simulated DOM elements and five HTTP contract checks for request
  forwarding, error-status bodies, network/JSON failures, and cancellation.
  The nine preference/loading checks cover: string/default behavior, denied access, malformed
  input, partial-write restoration, cleanup isolation, and script loading order
  for all three HTML entrypoints.
- Twenty-two browser checks passed across the standard and preview pages. Added
  cases cover local-provider host/model persistence, stale selections, legacy
  credential removal, blocked-storage error presentation, and tab keyboard
  activation, disposal, and remounting. Modal checks cover child versus backdrop
  clicks, native keyboard activation, and cleanup/remounting. Settings form checks
  verify all five handlers register once, cleanup detaches old elements, and
  remounting attaches replacements without calling services. Neutral DOM wrapper
  checks verify literal text (no HTML), null clearing, missing IDs, and replacements.
- Frontend syntax and diff whitespace checks passed; the new untracked module
  was also explicitly parsed with `node --check`.

The third diagnostic page has loading-order coverage, not full browser workflow
coverage. The tests use mocked services and do not verify live providers or
operational workflows. Hosted CI verification and the recorded Python baseline
blockers remain separate tasks.

See [JavaScript Test Inventory](../JAVASCRIPT_TEST_INVENTORY.md) for setup and
[the refactor checklist](../REPOSITORY_INFRASTRUCTURE_REFACTOR_CHECKLIST.md) for
remaining work.

## Settings controller module (2026-10-24)

`webapp/llm_settings.js` exports `ISRLLMSettings.create(options)` for classic browser
scripts and CommonJS `create(options)` for isolated tests. Construction returns
an independent controller without reading DOM/storage or starting requests,
listeners, or timers. Options supply `document`, the existing `storage`/`http`
adapters, `createModal(options)`, and `appendDebugLine(message)`. No application,
planner, weather, or mission state is injected. Existing console error reporting
uses the runtime console. All original methods and mutable fields are retained.

`isr.js` constructs a single lexical `LLMSettings` binding with lazy browser
storage/fetch/modal/debug callbacks. Existing Agent Send and `CompareUI` callers
still use `getCurrentSettings()`; their workflow/request code is unchanged.
Startup remains in the main script at the original position: DOMContentLoaded
when loading, otherwise immediate init. The module registers no separate startup.

`init()` still awaits provider loading before event setup and saved selection
restoration. Model discovery initiated by provider changes retains its existing
async behavior. Listener setup/disposal/remount and modal compatibility methods
retain the contracts above; disposal does not cancel in-flight work, hide the
modal, or suppress later responses. No retry, timeout, cancellation, persistence,
provider-selection, or markup policy was added by this move.

The shared template loads the new module after its helpers and before `isr.js`.
All three generated pages and the main-script query label were updated together.
The provider-request VM harness now loads the actual module plus the main-script
composition binding; it no longer extracts/copies a controller implementation.

Local validation: baseline 40 unit / 24 Chromium checks; after extraction 50 unit /
26 Chromium checks. Provider-request assertions, tracked syntax, explicit new-module
syntax, generated-page freshness, source preservation, documentation links, and
diff checks passed. The browser suite uses local assets/mocked services; it does
not verify real providers or operational workflows. No full Python, hosted CI,
commit, merge, or deployment is claimed. See the extraction checklist for review
evidence, remaining candidates, and rollback.

## Debug text writer (2026-10-24)

`webapp/debug_output.js` exports `ISRDebugOutput.create({ getOutput, formatTime })`
in classic browser scripts and CommonJS `create`. It returns an inert writer with
`prepend(message)`. Construction invokes neither dependency. Each write looks up
the current output element; if absent it returns `undefined` before touching the
clock or converting the message. Otherwise it calls `formatTime()` once and sets
`textContent` to `[time] message\n` followed by existing text. Text is literal,
newest first, and not truncated. Lookup, formatting, interpolation, and assignment
errors propagate. There are no events, timers, persistence, or disposal methods.

The main script constructs one writer after its DOM helper, before other
initialization, with lazy `$` lookup for `debug-output` and
`new Date().toLocaleTimeString()`. The global `appendDebugLine(msg)` compatibility
function delegates to it. Callers in the main script, Cesium, weather preview,
and checkpoint controller are unchanged; settings retains its injected callback.
No caller messages, domain state, operational hooks, or logging-disclosure policy
were changed. This is a presentation extraction, not a redaction feature.

The shared template loads the writer before `isr.js`; all page variants are
regenerated with updated asset labels. Local validation: 53 unit / 28 mocked
Chromium checks, provider-request assertions, page freshness, syntax, source
preservation, links, and diff checks passed. The browser checks verify the real
wrapper, default locale formatter invocation, literal text, replacement elements,
and the missing-element guard. No live service or operational scenario was run.
The extraction checklist records the baseline and rollback scope. No commit,
merge, or deployment was performed.

## Voice control wiring (2026-10-24)

`webapp/voice_controls.js` exports `ISRVoiceControls.create(options)` / CommonJS
`create`. Dependencies are `document`, `getById`, `getVoice`, and `fetchRequest`.
Construction is inert. Browser voice/fetch lookups remain lazy through the main
script's injected functions. There is no planner/agent state in the module.
`voice.js` still owns engines, audio requests/downloads, and engine fallbacks.

The controller exposes the four original helper names: `speakRepliesEnabled`,
`wireReplyVoice`, `attachVoiceHandlers`, and `attachTtsEngineSelector`. Global
functions in `isr.js` delegate to one controller; startup and reply consumers
remain unchanged. Labels, dictation concatenation/final focus, listening classes,
preload/cancel ordering, per-reply autoplay/replay, engine option labels/disabled
state, default selection, endpoint, and failure fallback are preserved.

The two mount helpers are now repeatable for their owned listener groups. The
engine selector fetches once per mount. `dispose()` removes those listeners and
releases control references; dispose before replacing controls, then remount.
It does not cancel speech, stop dictation, hide/reset controls, cancel pending
fetches, or suppress their callbacks. Old pending responses retain the previous
behavior, including updating the originally captured select and engine.

Reply wiring still overwrites the button's `onclick` and can autoplay on each call.
`disposeReplyVoice(qaBlock)` optionally clears only the reply handler still owned
by this controller. A WeakMap tracks ownership without retaining detached replies.
Control disposal does not iterate/clear all replies or imply full asynchronous
shutdown; consumers can dispose individual reply handlers when needed.

The shared template loads this module before `isr.js`. Local validation: 61 unit /
30 Chromium checks, provider-request assertions, freshness, explicit/tracked
syntax, source preservation, documentation links, and diff checks passed.
Browser voice cases replace the actual engine script with fake engines and use
neutral text. They do not access microphones, play speech, download models, or
exercise operational workflows. See the extraction checklist for baseline,
acceptance, limitations, and rollback. Nothing committed, merged, or deployed.

## Trace display renderer (2026-10-24)

`webapp/trace_view.js` exports `ISRTraceView.create({getContainer, escapeHtml,
formatTime})` / CommonJS `create`, returning an inert `render(runs)` view. It
lazily resolves its container and formats supplied records without storing or
mutating them. Missing containers return before inspecting records; falsy/empty
collections retain the existing empty message. Rendering preserves newest-first
run order, event order, field fallback/truncation, optional ID/data display,
error/warn/info classes, and injected escaping/time formatting. Malformed-data,
dependency, and serialization errors retain their existing propagation behavior;
no partial HTML replaces the old content before a completed render.

The main script creates one view with the existing `$` lookup, shared `escapeHtml`,
and `new Date(ts).toLocaleTimeString()`. `renderAgentsTraceTab()` delegates with
current `state.agentTraceRuns`. Trace creation, comparison aggregation, state
ownership, clear confirmation/state replacement, and debug messages remain in
`isr.js`. The module owns no listeners/timers/requests and needs no disposal.
There is no new validation, persistence, retention, or operational interpretation.

Four new isolated checks and two neutral-record Chromium checks verify the
boundary. Local unit count is 65; the final full Chromium rerun passed all 32
checks, with the initial map-timer finding/rechecks documented in the extraction
checklist. No provider
calls or operational workflows are required. The shared template/generated pages
load the view before the main script; changed asset labels use
`20261024-trace-view`. Syntax, freshness, caller assertions, source preservation,
links, and diff checks pass. No commit, merge, or deployment was performed.


## Leaflet delayed resize ownership (2026-10-24)

This focused lifecycle follow-up leaves the map integration in `isr.js`.
`mapState.resizeTimer` owns the one pending 200 ms size invalidation. The callback
checks that its map is still current and releases the timer handle before calling
`invalidateSize`. `destroyLeafletMap` cancels/releases pending work before removing
tiles and the map; repeated teardown is safe. Reopening creates a fresh map and
fresh timer. Map options, synchronization, and operational workflows are unchanged.

`leaflet-lifecycle.spec.mjs` uses local real Leaflet, neutral fixtures, and a
controlled browser clock on standard/preview pages. It reproduced the recorded
removed-map failure before the fix and checks cancellation, immediate replacement,
repeated initialization/teardown, and exactly one live-map resize at 200 ms.


## Chat card and system-message view (2026-10-24)

`webapp/chat_view.js` exports `ISRChatView.create({document, getHistory, escapeHtml,
formatTime, createMessageId})` and CommonJS `create`. Construction is inert. The
returned `createQABlock(question)` creates a detached `.qa-block`, escapes its
question, and preserves the timestamp, labels, thinking state, and hidden replay
button. The caller still inserts the card, sends requests, renders replies, and
wires voice behavior. This view owns no listeners, state, requests, or timers.

`appendChatMessage(content)` remains a legacy caller-supplied HTML interface,
returning the generated message ID, appending to current history, and scrolling
to its bottom; absent history returns `null` before ID generation. It does not
introduce a sanitizer or change the HTML policy. `removeChatMessage(id)` resolves
the current document node lazily and is a no-op for falsy/missing IDs. Dependency
failures propagate as before; no error recovery is added.

`isr.js` composes one view, retains all three global compatibility functions and
shared `escapeHtml`, injects the existing hour/minute locale-time and Date/Math ID
expressions, and leaves Agent Send and its provider-request test harness unchanged.
The other two helpers have no production call sites found and remain for
compatibility. The module needs no startup/teardown. Unit contracts use neutral
fixtures; standard/preview checks verify actual DOM escaping, labels, detached
construction, caller HTML, scroll/order, removal, and lazy replacement/missing
history. No agent request, reply interpretation, or operational scenario is needed.


## Fleet drawer disclosure controller (2026-10-24)

`webapp/fleet_drawers.js` exports `ISRFleetDrawers.create({document, getById})`
and CommonJS `create`. It owns only the click listeners for existing drawer
buttons and Collapse all. Construction is inert; `attachFleetDrawers()` captures
current buttons once per mount. `dispose()` removes owned listeners, is repeatable,
and preserves display. Dispose before replacing controls, then remount. Mounting
without controls is safe but does not observe future nodes; dispose/remount is
required. No whole-application remount/teardown contract is added.

`setDroneDrawer(did, open)` lazily resolves current nodes, even after disposal,
then applies existing hidden/ARIA/arrow/row-class/collapse-visibility presentation.
It permits independent open rows and preserves the existing five-row Collapse all
range and missing-node behavior. There is no enabled-state policy in the module;
native disabled buttons remain inactive, while `updateFleetRowStates` in `isr.js`
continues to own configuration-derived disabling and closing. DOM exceptions
propagate without new recovery behavior.

The main script composes one controller and retains both global entrypoints.
`attachConfigListeners` owns the existing mount call; configuration writes,
state/calculations, and other listeners remain in place. Disposal affects only
this controller, with no timers, network, storage, or asynchronous shutdown.


## Memory-list display view (2026-10-24)

`webapp/memory_view.js` exports `ISRMemoryView.create({getList, escapeHtml})` and
CommonJS `create`, returning inert `render(memories)`. It resolves the current list
on each render and returns early if absent. Empty markup/text/style, record order,
IDs, category interpolation, escaped content, and delete-button markup are retained.
The renderer does not mutate supplied records; malformed-input/dependency errors
propagate as before. No validation, HTML-policy change, retention, or requests are
added. It emits the existing inline `deleteAgentMemory(id)` action, which requires
the application's retained global entrypoint. No new listener/subscription or
separate disposal contract is introduced.

`isr.js` composes one view and retains `renderMemoryList` as a delegate. Its sole
direct caller remains `loadAgentMemories`; fetching, count updates, add/delete/clear,
confirmations, panel toggling, input handling, debug logging and startup remain
unchanged in the application. Thus memory actions are still mixed integration;
this move selects display only. Browser checks temporarily replace the delete
callback with a neutral ID recorder and restore it without issuing memory writes.


## Viewport preference persistence (2026-10-24)

`webapp/view_preferences.js` exports `ISRViewPreferences.create({getStorage, now,
storageKey, ttlMs, defaultZoom, normalizeView})` and CommonJS `create`. It is inert
until `save(view)`/`read()` is called and owns only a per-instance duplicate key.
The application injects `isrLastView`, 30 days, zoom 13, lazy browser storage/clock,
and the existing coordinate-migration callback. That callback, all map state,
projection/constraining/application, geolocation, startup precedence, import/export,
and callers remain in `isr.js`. Global save/read entrypoints remain delegates.

Saving compares the original five/five/one-decimal key before writing unrounded
`centerLat`, `centerLng`, `zoomLevel` and `timestamp`. It updates the key before the
write; denial does not trigger retries for that rounded view. Formatting errors
occur outside the catch; storage/clock/JSON errors during a write are swallowed.
Reads retain existing type/timestamp checks, strict `age > ttl` expiry, nullish
zoom fallback, and normalization within the catch boundary. No finite/range or
future-time policy, transactional rollback, or cross-tab synchronization is added.
This stays separate from the transactional string-preference adapter because its
existing error and duplicate-suppression behavior differs.

The main script composes the store after its constants without reading view state.
The coordinate caller harness loads the actual module and production composition;
its assertions remain unchanged. Browser fixtures now seed a neutral view only
when absent, allowing reload checks to verify actual persistence rather than
replacing saved values at every navigation. Tests use neutral views, no geolocation,
provider calls, or scenario execution. Per-instance ownership needs no disposal;
reload creates a fresh duplicate key while preserving the stored record.


## General UI construction boundary (2026-10-26)

`frontend_ui.js` now constructs the seven adjacent general UI instances; the main
script supplies named factories and lazy adapters and retains the same lexical
bindings/compatibility entrypoints. It owns no startup or application lifecycle.
Settings and viewport construction remain at their existing locations. See
[General UI Composition](FRONTEND_UI_COMPOSITION.md) for interface/caller evidence,
neutral tests, validation and rollback. Architecture stages A/B are complete for
this scope; lifecycle inventory and further stages remain planned.


## General UI lifecycle ownership review (2026-10-26)

Stage C documents the existing lifecycle interfaces in
[General UI Lifecycle](FRONTEND_UI_LIFECYCLE.md). Tabs, settings/modal, voice,
per-reply voice and drawer cleanup retain their individual method names and
ownership. Stateless presentation, composition and viewport storage gain no
lifecycle API. The review adds characterization of unrelated-listener preservation,
replacement remount and pending async/callback behavior; it changes no runtime
module, startup sequence or application workflow. Stage D next reviews general
presentation adapters within the same retained boundaries.


## General presentation adapter review (2026-10-26)

Stage D documents the existing [Presentation Adapters](FRONTEND_PRESENTATION_ADAPTERS.md):
borrowed trace/memory records, display snapshots, DOM lookup, clocks/ID generation,
escaping versus legacy HTML and retained callback ownership. Added tests verify
explicit rerender with mutable records and composed real-DOM presentation using
an isolated document without engine/transport access or host-UI changes. Existing
runtime modules, supplied-value interfaces, wrappers, callers, state and workflows
are unchanged. Stage E next evaluates and closes the selected architecture scope.


## Architecture iteration closeout (2026-10-26)

Stage E evaluates and closes the selected general UI architecture scope. The
[architecture closeout](FRONTEND_ARCHITECTURE_CLOSEOUT.md) records achieved goals,
source-scope audit, retained/deferred integration and validation limits. Current
construction/lifecycle/presentation contracts remain implemented as documented.
Remaining shared state and operational code are not declared refactored. No new
runtime boundary or automatic follow-on extraction is selected.
