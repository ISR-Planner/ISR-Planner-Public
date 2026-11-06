# General presentation adapter contracts

Status: **stage D documented and locally verified**, 2026-10-26. Contract selected
before adding characterization tests under the
[Frontend Architecture Plan](FRONTEND_ARCHITECTURE_PLAN.md). The review describes
implemented interfaces and adds characterization evidence. No runtime interface,
formatting policy, application state or transport ownership change is selected.

## Selected boundary and callers

The selected views are debug output, trace display, memory-list display and chat
presentation, plus their existing DOM and formatting adapters. They are composed
through [General UI Composition](FRONTEND_UI_COMPOSITION.md). Drawer disclosure
accepts an explicit ID/open value and retains its existing direct setter. Control
listener ownership remains in [General UI Lifecycle](FRONTEND_UI_LIFECYCLE.md).

| Compatibility entrypoint | Supplied-value interface | Ownership / return contract |
| --- | --- | --- |
| `$`, `setText`, `escapeHtml` | `domHelpers.getById(id)`, `setText(id, value)`, `escapeHtml(value)` | Lazy DOM access; existing missing-node/coercion/serialization behavior. No application data owner. |
| `appendDebugLine(message)` | `debugOutput.prepend(message)` | Literal text, injected time, newest-first prepend; returns `undefined`. Caller retains message construction and disclosure policy. |
| `renderAgentsTraceTab()` | `traceView.render(state.agentTraceRuns)` | Wrapper alone reads current application trace state; view receives borrowed records, renders newest-first, returns `undefined`. Capture/aggregation/clearing stay with callers. |
| `renderMemoryList(records)` | `memoryView.render(records)` | Borrowed ordered records, no count update; returns `undefined`. Fetching/actions/count/panel state stay in the application. |
| `createQABlock(question)` | `chatView.createQABlock(question)` | Returns a detached element; caller inserts it and owns reply population/voice wiring. |
| `appendChatMessage(content)` | `chatView.appendChatMessage(content)` | Existing caller-supplied HTML interface; appends/scrolls and returns generated ID, or `null` for absent history. No current production call site found outside retained compatibility entrypoint. |
| `removeChatMessage(id)` | `chatView.removeChatMessage(id)` | Existing document ID lookup/removal; falsy/missing IDs are no-ops, returns `undefined`. No automatic reply-handler/engine cleanup. |
| `setDroneDrawer(id, open)` | `fleetDrawers.setDroneDrawer(id, open)` | Caller supplies the existing ID and boolean; updates DOM/ARIA/labels/class/collapse visibility. Configuration-derived policy stays with its caller. |

The actual globals remain compatibility delegates. Settings and later classic
scripts still receive the existing callbacks. No wrapper removal or move from
lexical bindings to `window` properties is selected. Existing browser composition
checks verify delegate instance/argument identity.

## Borrowed records and display snapshots

Trace and memory renderers read the supplied arrays/records synchronously. They
do not sort, mutate, freeze, copy into application state, subscribe to, or own
those records. The markup is a display snapshot, not a reactive model. Changing a
record after a render must not change the existing DOM until the caller renders
again. Calling render again with the same mutable records reads their current
values. Existing frozen-input tests continue to verify non-mutation.

The views do not retain an input collection in controller state. That is a source
inventory finding, not a garbage-collection guarantee. DOM may contain serialized
values and inline action attributes after rendering. Replacing/clearing that DOM
does not mutate the supplied records or cancel any application request.

Missing list/container lookup returns before reading supplied records or invoking
formatters. Empty/nullish collections retain their existing empty display. No new
schema normalization, validation, retention limit or fallback behavior is added.
Malformed inputs and dependency/JSON errors keep their existing propagation;
existing tests cover failure behavior without operational fixtures.

### Observed trace input shape

Each run supplies `ts`, string `user_message`, optional string `mission_id`, and
`trace_events`. Events use existing `t || type || ''` and `msg || message || ''`
fallbacks; truthy `data` is serialized as JSON. Run order is displayed newest-first;
event order is retained. Labels truncate as currently implemented and CSS severity
classes follow the existing event-type substrings. These are display rules only;
no workflow interpretation or new data-model authority is added.

### Observed memory input shape

Each record supplies `id`, `category` and `content`. Record order is preserved;
content uses the injected text-escaping adapter. The existing ID/category template
interpolation and inline `deleteAgentMemory(id)` button action remain. The global
action belongs to the application and is not a callback registry owned by the view.
A neutral numeric-ID test already verifies dispatch without memory writes. This
review does not change ID/category validation or migrate the inline action.

## DOM and formatting adapters

| Adapter | Current timing and policy |
| --- | --- |
| `getById` and fixed-ID closures | Resolve current nodes for each direct operation. No document-node lookup during composition; replacement/missing-node behavior remains. |
| `escapeHtml` | Delegates through retained helper to the DOM utility's detached-div text serialization. HTML text escaping preserves native conversion and unchanged quotes; it is not a newly introduced attribute encoder or sanitizer. |
| `formatDebugTime` | Existing zero-argument Date locale expression; called only when an output node exists. Message output uses `textContent`. |
| `formatTraceTime(ts)` | Existing Date(ts) locale expression; result is escaped in trace markup. Called at render time with the supplied timestamp. |
| `formatChatTime()` | Existing hour/minute locale expression; its result remains interpolated into the card label as before. Question text is escaped separately. |
| `createMessageId()` | Existing Date/Math expression; generated only after current chat history exists. No new ID format, uniqueness, persistence or retry policy. |

These contracts distinguish literal text, escaped text and caller-supplied HTML.
They do not claim that all template fields use the same encoding policy. Legacy
system-message HTML, memory ID/category markup, and inline actions retain their
existing behavior. No sanitization/formatting redesign is bundled into this review.

The composition root passes clock/ID expressions and lazy lookup/escaping callbacks
from `isr.js`. It passes engine lookup and fetch only to the voice control factory;
selected views have no transport/storage/state dependency. New view contracts must
keep supplied display values and explicit presentation callbacks as their boundary.
Do not pass a whole application state object to avoid defining a view's inputs.

## Evidence selected for stage D

Existing contracts already verify escaping, ordering, truncation, optional values,
malformed-input behavior, detached cards, native formatting, missing/replacement
nodes, legacy HTML and delete-button dispatch. Stage D adds the missing temporal
and composed-boundary evidence rather than duplicating each renderer's cases:

- Trace/memory tests reuse mutable records across explicit renders, verifying
  snapshot timing, refreshed values/IDs/events, and continued input non-mutation.
- Standard/preview browser cases compose the real modules against an isolated
  HTML document with neutral records and supplied clocks/IDs. Presentation calls
  must not access voice/transport callbacks or modify the host application's DOM.
- The composed test verifies literal/escaped/legacy-HTML contracts, detached card
  creation, and borrowed-record refresh. It does not invoke inline memory actions
  or introduce an application-state adapter.

## Disposition and acceptance

No new runtime adapter is needed: the existing views already expose the selected
supplied-value interfaces. Reusable view ownership is explicit; memory/trace
writers/actions, configuration, domain-dependent presentation and workflow code
remain retained. Whole-application view models, event buses, request adapters and
state migration are not selected.

- [x] Inventory selected value/callback interfaces and current wrapper consumers.
- [x] Document borrowed-value/snapshot, formatting/HTML, missing-node and failure
  contracts without changing their runtime policy.
- [x] Verify mutable-record reuse and composed real-DOM isolation using neutral
  values, without state/transport/engine authority or application DOM changes.
- [x] Preserve existing view/wrapper/caller/startup/source behavior and prior tests.
- [x] Run relevant baseline/post-review checks and record limits and handoff.

Rollback removes added characterization tests and documentation/status changes.
There is no runtime, stored-record, script-label or generated-page migration.


## Verification record (2026-10-26)

Fresh baseline: 97 infrastructure unit / 48 Chromium checks passed, plus
provider-request assertions, tracked frontend syntax and page freshness. After
review: **99 unit / 50 Chromium checks passed**, browser retries disabled
(33.5 seconds). Two added unit cases and two standard/preview-hosted real-DOM
cases verify the selected temporal and composed-boundary gaps. Existing view,
composition, lifecycle and caller assertions remain unchanged and passing.

Only tests and documentation changed. Runtime modules/interfaces, main-script
wrappers/callers, construction/startup, page templates/generated outputs, storage,
requests, application state and workflows are unchanged. No new adapter module,
view model, subscription, transport/callback registry or script label is introduced.
The current interfaces already satisfy the selected supplied-value boundary.

New/changed test syntax, frontend syntax (34 tracked scripts), three-page freshness,
documentation links and diff checks passed. No full Python, live-provider,
real-engine, operational-scenario or hosted CI result is claimed. No commit,
merge or deployment was performed. Stage E evaluates and closes the selected
architecture scope; the small-extraction pass remains closed.
