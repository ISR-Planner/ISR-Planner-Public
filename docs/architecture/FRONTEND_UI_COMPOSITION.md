# General UI composition boundary

Status: **implemented and locally verified**, 2026-10-26. Contract selected before
source movement for stages A/B of the
[Frontend Architecture Plan](FRONTEND_ARCHITECTURE_PLAN.md). This is a construction
boundary for existing general UI components, not a new application startup owner.

## Observed construction and callers

The selected top-of-file constructions in `isr.js` are, in order, DOM helpers,
debug output, voice controls, trace view, chat view, fleet drawers and memory view.
Existing compatibility functions refer to those lexical instances. Other classic
scripts and inline actions retain their current entrypoints. A caller search found
no legacy test slicing these seven declarations. Existing tests exercise wrappers
or stub their dependencies; the settings and coordinate harnesses retain their
separate composition locations.

Construction is inert today. DOM/control mounting happens in existing startup
callers, not in these factories. Settings and comparison/GNN have their existing
DOM-ready branches; `page_start.js` initializes selected extensions before the
main `window.load` callback. Viewport composition stays after its constants;
settings composition and all ready-state branches stay in place.

## Selected interface

`ISRFrontendUI.create(options)` / CommonJS `create(options)` receives:

| Dependency | Contract |
| --- | --- |
| `document` | Borrowed existing document, passed only to components that already require it |
| `factories` | Named `dom`, `debugOutput`, `voiceControls`, `traceView`, `chatView`, `fleetDrawers`, `memoryView` namespaces, each with existing `create` |
| `getById`, `escapeHtml` | Lazy adapters to the retained main-script compatibility functions |
| `getVoice`, `fetchRequest` | Existing lazy engine lookup and fetch forwarding for voice controls |
| `formatDebugTime`, `formatTraceTime`, `formatChatTime`, `createMessageId` | Existing Date/locale and Date/Math expressions, supplied by the application |

It returns exactly `domHelpers`, `debugOutput`, `voiceControls`, `traceView`,
`chatView`, `fleetDrawers`, `memoryView`: the instances returned by those factories,
without wrappers, copies, freezing, or replacement state. Construction preserves
order and invokes each factory exactly once per `create` call. The application
calls it once at the old top-of-file boundary and destructures the same lexical
bindings; these are not converted to `window` properties.

The boundary supplies the existing fixed DOM IDs through lazy closures and passes
other adapters through. It does not resolve a DOM node, engine, request, clock,
application state or storage at construction. It owns no default browser globals,
singleton cache, mount/init/dispose API, listener, timer or async shutdown promise.
Component construction failures propagate and stop subsequent construction, as
before; there is no new rollback or retry policy.

## Implementation acceptance

- [x] Record a fresh relevant baseline and preserve existing working-tree changes.
- [x] Add contracts for construction order, instance/dependency identity, lazy
  adapters, inert real-component composition and failure propagation before moving source.
- [x] Verify the actual main-script composition under loading/complete document
  states, including lexical bindings and late callback/global lookup.
- [x] Introduce the composition factory and retain all main-script wrappers,
  settings/viewport composition, startup, state and workflow implementations.
- [x] Load the new factory after its seven dependencies and before `isr.js` through
  the shared template; refresh changed labels and regenerate all three variants.
- [x] Verify standard/preview one-time construction, exact instance consumption by
  existing delegates, and existing browser behavior with neutral fixtures.
- [x] Review source preservation, run relevant checks and update dated results.

The two ready-state characterization cases cover this construction block only;
existing settings tests cover settings initialization branches. No general startup
or full-application lifecycle contract is added by these tests.

## Rollback

Restore the seven construction declarations, remove the composition script from
the shared template, regenerate all page variants, restore changed asset labels,
and revert associated tests/docs together. Do not construct old and new bundles
simultaneously or reset stored preferences. No data migration is involved.


## Validation and implementation record (2026-10-26)

Fresh baseline: 86 unit / 44 Chromium checks passed, plus provider-request,
tracked syntax and page freshness. After composition: **92 unit / 46 Chromium
checks passed**, browser retries disabled (26.7 seconds for Chromium).

Six new isolated checks verify exact construction order/instances/adapters, lazy
fixed-ID lookup and errors, construction failure propagation, inert real-component
construction, and actual main-script lexical/lazy behavior for loading and complete
document states. Two new standard/preview cases observe one composition call and
one construction per component, then check that existing delegates consume those
exact instances and forward supplied values/identities. Test-only observation and
temporary stubs are restored; production has no instrumentation or method patching.
No agent/memory writes, microphone, speech output or real-provider call is required.

Source comparison with the pre-step file confirms only the top-of-file construction
block changed in `isr.js`. All compatibility wrappers, settings/viewport composition,
DOM-ready/load branches, application state and workflow implementations are
unchanged. No source-slicing consumer needed adaptation for the selected block.
The shared template loads `frontend_ui.js` after its dependencies and before the
main script; both changed labels use `20261026-composition`. All three generated
variants have loading/order coverage and freshness checks.

Provider-request assertions, 33 tracked-script syntax checks, explicit new-module/
test syntax, source preservation, documentation links and diff checks passed.
No full Python, live-service, real-engine, operational-scenario, hosted CI, commit,
merge or deployment result is claimed. The earlier small-extraction pass stays
closed. Stage C's lifecycle inventory is the next candidate; no new application-wide
lifecycle or async shutdown API is implemented here.
