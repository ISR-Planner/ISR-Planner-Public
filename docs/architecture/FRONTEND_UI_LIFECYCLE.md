# General UI listener ownership and lifecycle

Status: **stage C documented and locally verified**, 2026-10-26. Contract selected
before adding characterization tests under the
[Frontend Architecture Plan](FRONTEND_ARCHITECTURE_PLAN.md). The existing narrow
controller methods are the lifecycle interface. No aggregate application teardown,
new startup owner, or runtime behavior change is selected by this review.

## Ownership inventory

| Owner | Existing mount / entrypoint | Owned cleanup | Preserved state and boundaries |
| --- | --- | --- | --- |
| Tabs (`tabControls`, lazy creation in `attachTabSwitching`) | `mount()` via `attachTabSwitching()` at existing load startup | `dispose()` removes captured tab-button click listeners | Active classes and current pane are preserved. Buttons/panes are captured at mount; dispose/remount after replacement. No async work. |
| Settings modal (`LLMSettings.modalControl`) | `getModalControl().mount()` during settings form setup | Modal `dispose()` removes open/close/backdrop click listeners only | Visibility and selections remain; direct open/close remain callable. Modal alone does not own form actions. |
| Settings controller (`LLMSettings`) | `setupEventListeners()` mounts modal and form handlers; `init()` loads providers before setup and preference restoration | `disposeEventListeners()` removes form and modal listeners | Provider/model selections, cache, status, visibility and storage persist. Cleanup does not cancel init/model discovery/testing. |
| Voice controls (`voiceControls`) | `attachVoiceHandlers()`; nested/optional `attachTtsEngineSelector()` | `dispose()` removes mic/speak-toggle/engine-selector listeners | No engine stop/cancel, visibility/title reset, request cancellation or STT callback revocation. Pending engine discovery may still update its captured selector and engine. |
| One reply voice button | `wireReplyVoice(block, text)` via existing wrapper | `disposeReplyVoice(block)` clears `onclick` only if still owned by this controller | General voice disposal does not clean reply buttons. Cleanup does not stop playback, hide/reset the button or remove another consumer's replacement handler. Weak ownership avoids retaining detached buttons. |
| Fleet drawers (`fleetDrawers`) | `attachFleetDrawers()` via existing configuration listener setup | `dispose()` removes captured toggle/collapse listeners | Open rows, arrows, ARIA, collapse visibility and disabled policy remain. Direct setter resolves current nodes even after disposal. |
| General UI composition (`frontend_ui.js`) | `create()` constructs seven instances only | No lifecycle API | The application holds existing instances and invokes their individual interfaces. No composed `mount`, `init`, `dispose` or async shutdown promise. |
| DOM/debug/trace/chat/memory views | Direct existing helpers / supplied-value render calls | No controller listener registry or disposal API | No mount subscription. Keep existing rendering/state ownership. Memory markup retains the inline global delete action; chat/trace callers retain their actions. |
| Viewport preferences | `save(view)`, `read()` | No disposal API | Store and duplicate key belong to the existing instance; no listener, timer or request. |

These are implemented interfaces, not new uniform names. Controllers receive the
existing document/elements and keep their existing identities. Settings and tabs
remain outside the seven-component composition bundle; viewport composition also
remains at its existing location.

## Mount and replacement contracts

For tabs, modal, settings form, drawers and voice control listeners, repeated
mount/setup on an already-mounted surface does not add a duplicate owned listener.
This is a controller-local guarantee, not a claim that application startup or all
`attach*` functions can be repeated safely.

The supported sequence for replacing a mounted surface is:

1. Call its existing owned cleanup method on the existing controller.
2. Replace the intended elements; cleanup does not reset their displayed values.
3. Invoke that controller's existing mount/setup method again.
4. If external code owns its own listeners on the new nodes, it must bind those
   separately; controller cleanup neither removes nor migrates them.

Captured old controls lose only the controller's handlers. Direct operations that
already perform lazy lookup retain that behavior. DOM replacement alone is not
an application disposal signal. Do not call broad configuration/agent/workflow
setup functions merely to remount an extracted control.

Missing-node details are intentionally different across controllers:

- Tabs/modal/settings/drawers and the voice-button group can record an empty or
  partial mount. Dispose/remount before expecting newly inserted controls to bind.
- The voice engine-selector helper returns before marking itself mounted if its
  selector or voice engine is absent. It can be called again once those exist;
  after a successful mount it needs disposal before replacement/remount.
- Cleanup leaves existing hidden/visible, title, class and selection values intact.
  It does not undo presentation effects of a previous mount or direct operation.

## Async work is separate from listener cleanup

`LLMSettings.init()` awaits provider loading, then sets up listeners and restores
preferences. Disposing listeners while that init is pending does not prevent its
later setup from mounting them. It is not a terminal shutdown API or a cancellation
barrier. Remounting settings should use `setupEventListeners()`, not a repeated
`init()` that also loads data and restores preferences.

Settings model discovery captures its model selector before awaiting transport.
A pending response can update that old node after disposal/replacement; no new
stale-response suppression, retargeting or abort policy is introduced here.
Connection tests/provider loads likewise retain existing completion behavior.

Voice disposal removes future DOM event dispatch to its handlers, but callbacks
already handed to STT can still update their captured input/button. Pending engine
discovery can still update its captured selector and select an engine. Reply-button
cleanup is per reply and separate from control disposal. No cleanup method in this
inventory promises to silence engines or await/cancel pending work.

These limits are compatibility contracts verified with neutral deferred promises
and fake voice engines. Changing them requires a separately scoped async-lifecycle
proposal; stage C does not add cancellation or an application-wide remount policy.

## Existing application boundaries retained

All current DOM-ready/load callbacks, page capability startup and caller locations
remain. Settings `init()` remains in its existing ready-state branches. Tabs mount
through `attachTabSwitching`; drawers through the current configuration setup;
voice controls through the existing agent control setup. Neither this document nor
the composition bundle adds calls to dispose during navigation or startup.

Memory actions, trace controls, map/engine lifetime, application/workflow state,
requests and domain-dependent listeners retain their existing owners. The earlier
Leaflet resize cleanup and Cesium subscription contracts are separate scoped work;
they do not establish a whole-application teardown facility.

## Acceptance and verification

- [x] Inventory current mount, cleanup, remount, state-preservation and async
  boundaries across the general UI owners.
- [x] Retain existing method names, runtime modules, callers and startup unchanged.
- [x] Verify tabs/modal cleanup leaves unrelated listeners intact and supports the
  currently promised repeated mount/disposal and replacement behavior.
- [x] Verify pending settings init/model responses and handed-out STT/reply handlers
  retain their documented behavior after listener disposal.
- [x] Verify standard/preview native keyboard dispatch through disposal/remount,
  unrelated listener preservation and replacement nodes without provider requests.
- [x] Run relevant unit/browser/caller/syntax/page/docs/diff checks and record limits.

This review uses the existing interfaces; no runtime lifecycle wrapper is required.
Rollback is removal of the added characterization tests and documentation updates.
There is no runtime migration, storage change or generated-page change to roll back.


## Verification record (2026-10-26)

Fresh baseline: 92 infrastructure unit / 46 Chromium checks passed, plus
provider-request assertions, tracked syntax and page freshness. After the review:
**97 unit / 48 Chromium checks passed**, browser retries disabled (30.1 seconds).
Five new unit cases and two standard/preview browser cases close the identified
ownership/async characterization gaps. Existing narrow cleanup/remount checks
remain intact; none were removed or weakened.

The changed files are tests and documentation only. Runtime modules, main-script
construction/callers, startup, page templates/generated outputs, storage and request
semantics are unchanged. No new script, lifecycle dispatcher or automatic cleanup
call was added. Existing interfaces already provide the selected narrow boundary.

New/changed test syntax, frontend syntax (34 tracked scripts), three-page freshness,
documentation links and diff checks passed. No full Python, real-engine,
live-provider, operational-scenario or hosted CI result is claimed. No commit,
merge or deployment was performed. Stage D is the next planned general
presentation-adapter review; the earlier small-extraction pass remains closed.
