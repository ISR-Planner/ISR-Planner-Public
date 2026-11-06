# Runtime function wrapper inventory

Reviewed 2026-10-21 for Phase 5. The tables below retain the initial source
inventory and line references. A subsequent change on the same date migrated the
two Cesium UI wrappers to `subscribeLifecycle`; **nine wrappers remain** (five in
preview, four in diagnostics). Mixed, operational, and diagnostic wrappers are
unchanged. See the [implemented lifecycle contract](SHARED_PAGE_SHELL.md#cesium-presentation-lifecycle-contract-2026-10-21).

## Scope and method

Here, a runtime wrapper captures an existing callable, replaces its binding or
property, and intercepts later calls. The review covers repository-owned JavaScript
under `webapp/`, including the three generated page entrypoints, shared scripts,
weather extensions, diagnostic runner, and retained segment modules. It excludes
third-party CDN implementations, test-only mocks, and backend decorators.

Reference searches covered saved-callable aliases (`original*`, `real*`, `orig*`,
`old*`, `previous*`), function-valued reassignment, prototype/property replacement,
and dynamic evaluation; matches were inspected to distinguish wrappers from new
callbacks and ordinary state assignments. Page reachability was checked against
`frontend/templates/planner.html` and `frontend/page-variants.json`. This is a
manual inventory, not an automated guarantee against future or computed wrappers.

The initial review found **11 replacement sites**: seven in `weather_preview.js` and four
in `_weather_diagnostics/runner.js`. No additional capture-and-replace wrappers
were found in the other reviewed application scripts. At that review, standard installed none of these 11, preview installed seven,
and diagnostic installed all 11. After migration, the counts are zero, five, and nine.

## Preview wrappers

References are relative to `webapp/`. Line numbers describe the reviewed source;
search by the named binding when code moves.

| Replacement site | Captured callable and behavior | Classification and disposition |
| --- | --- | --- |
| `weather_preview.js:39`, `cesiumView.init` | `originalInit`; forwards arguments, awaits initialization, retains and returns the viewer for weather drawing | Migrated to lifecycle `onInit`; captures the initialized viewer without replacing the method. Completion/rejection behavior is preserved. |
| `weather_preview.js:40`, `cesiumView.render` | `originalRender`; runs the base render, then `render3d()` to restore weather entities. The wrapper currently returns `undefined`. | Migrated to lifecycle `onRender`; base-before-overlay ordering is verified. |
| `weather_preview.js:49`, `syncViewStateFromLeaflet` | `originalSyncMap`; runs the base synchronization, remembers bounds, and may mark the weather snapshot dirty; returns the base result | Mixed map UI and operational snapshot validity. Preserve the whole wrapper; do not migrate it as a generic map notification in this phase. |
| `weather_preview.js:215`, `loadFromJson` | `originalLoadFromJson`; handles demo imports or delegates regular imports, then updates weather source/window state | Operational import integration. Preserve. |
| `weather_preview.js:287`, `buildSolveRequest` | `originalBuildSolveRequest`; delegates request construction and adds weather-dependent request state | Operational request construction. Preserve. |
| `weather_preview.js:304`, `acceptSolution` | `originalAcceptSolution`; checks weather consistency before delegation | Operational acceptance integration. Preserve. |
| `weather_preview.js:339`, `runPlanner` | `originalRunPlanner`; applies weather gating around execution and updates weather follow-up state | Operational planning integration. Preserve. |

The classification concerns the interception point, not permission to change the
weather renderer, its data selection, or any domain calculations. The first two sites were the general UI lifecycle candidates and are now migrated.

## Diagnostic wrappers

These install only when `weatherDiagnostics` is enabled, after the preview
initializer. Diagnostic code remains outside the general UI migration.

| Replacement site | Captured callable and behavior | Classification and disposition |
| --- | --- | --- |
| `_weather_diagnostics/runner.js:30`, `window.fetch` | `realFetch` bound to `window`; intercepts diagnostic weather/solve requests and delegates other requests | Diagnostic operational transport interception. Preserve; not a general HTTP adapter hook. |
| `_weather_diagnostics/runner.js:42`, `window.requestAnimationFrame` | `realRAF` bound to `window`; records timing for callbacks named `animate`, then invokes the callback. The replacement currently does not return the native request ID. | Diagnostic timing instrumentation coupled to playback. Preserve; not a generic presentation lifecycle hook. |
| `_weather_diagnostics/runner.js:50`, `loadFromJson` | `originalLoad`; captures the **preview-wrapped** importer, adds diagnostic import/source state, and returns the delegated result | Diagnostic operational import integration. Preserve the wrapper chain. |
| `_weather_diagnostics/runner.js:59`, `#wp-get.onclick` | `originalGetWeather`; captures the preview-installed handler, prepares recorded weather/window state, awaits the handler, and updates diagnostic status | Diagnostic operational UI action. Its DOM location does not make it a general UI hook. Preserve. |

## Initialization and ownership

`page_start.js` initializes registered extensions synchronously before window
load. Preview subscribes to the Cesium lifecycle and captures the remaining core functions first. Diagnostics
then captures the preview importer and weather button handler, plus the native
fetch/frame functions. Calls through diagnostic `loadFromJson` therefore delegate
to preview and then core; changing this order would change behavior.

The wrappers live for the page lifetime. They have no disposal/restoration API.
`PageCapabilities` rejects repeated initialization, preventing these extensions
from stacking wrappers through duplicate startup. This does not provide recovery
from partial initializer failure or asynchronous shutdown.

The following related mechanisms are **not additional runtime wrapper sites**:

- `weatherCheckpoints.configure(...)` supplies explicit controller callbacks; it
  does not capture and replace prior callables. Keep this operational contract.
- Preview's window-load listener, follow interval, map listeners, and initial DOM
  handlers register new callbacks. Their lifecycle remains separate work.
- `window.weatherPreview` and `window.weatherDataSource` expose extension APIs;
  optional calls from core are consumers, not capture-and-replace wrappers.
- `$`, `setText`, `attachTabSwitching`, and settings `openModal`/`closeModal` are
  compatibility delegates created in Phase 4. They remain covered by that phase's
  caller-migration requirements; they are not monkey patches to remove here.

## Implemented general UI migration

The Cesium initialization/render lifecycle now has one explicit observer interface.
Viewer capture and overlay drawing migrated together. The shared-shell contract
defines ownership, removal, ordering, errors, and viewer recreation behavior.

The current production call sites in `isr.js` are:

| Method | Consumers to verify |
| --- | --- |
| `init` | `toggle3dMode` (`:761`), whose promise continuation configures imagery and calls render |
| `render` | 3D activation (`:762`), general canvas redraw (`:5927`), platform update (`:7838`), altitude input change (`:8542`), tanker altitude update (`:9636`), and playback-related scene refreshes (`:15464`, `:15534`) |
| `destroy` | 3D deactivation in `toggle3dMode` (`:769`); no current weather wrapper, but the extension retains the old viewer until a subsequent init |

Preserve all caller arguments, including `{ keepCamera: true }`, existing return
values, and base rendering behavior. The weather renderer also runs directly
through its own `draw()` function; a new lifecycle subscription must not remove
those paths or introduce duplicate draws.

The migration adds focused lifecycle checks using neutral viewer/overlay
fixtures and dedicated browser checks for initialization, render ordering,
disposal, and reopening the view. The existing infrastructure Chromium suite
serves an empty Cesium stub and never opens 3D, so its passing result alone cannot
verify this migration. Live weather, operational scenarios, and diagnostic timing
runs are not required to test a general lifecycle contract.

## Verification and status

This inventory was checked against source assignments, page script composition,
and Cesium call-site references. No application, generated HTML, or tests changed
for this inventory step; no new behavioral test run is claimed. The previous
capability-startup validation remains recorded in [Shared Page Shell](SHARED_PAGE_SHELL.md).
The follow-up general UI interface work is now locally verified as recorded in
the shared-shell contract. The initial inventory itself changed documentation
only; rollback of the implementation must also restore the API, extension, tests,
asset URLs, and generated pages together. Nothing has been committed, merged, or
deployed by this follow-up.
