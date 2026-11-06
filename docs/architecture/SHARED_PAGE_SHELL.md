# Shared page shell: Phase 5 inventory and design

Status: static shell generation implemented on 2026-10-19. Shared markup now
comes from one template; generated HTML preserves existing page behavior. Explicit runtime capability startup and the general UI lifecycle migration are
implemented. Mixed, operational, and diagnostic hooks retain their existing boundaries.

## Current page variants

| Property | Standard | Weather preview | Weather diagnostic |
| --- | --- | --- | --- |
| File under `webapp/` | `index.html` | `weather_preview.html` | `_weather_diagnostics/index.html` |
| Public URL | `/` and `/static/index.html` | `/static/weather_preview.html` | `/static/_weather_diagnostics/index.html` |
| Browser title | ISR Web Editor + Planner | ISR Web Editor + Planner | ISR Weather Speed Diagnostic |
| Visible header | ISR Mission Editor & Planner (Web) | ISR Planner · Weather Preview | ISR Planner · Weather Preview |
| Weather panel | Absent | Present before Allocation in Env tab | Same as preview |
| Weather scripts | Absent | Checkpoints, then preview | Same as preview |
| Diagnostic runner | Absent | Absent | Last extension before shared startup |
| Elements with IDs | 278 | 313 | 313 |
| External script tags | 22 | 24 | 25 |

All 278 standard-page IDs are present in the preview. The 35 extra IDs belong to
the weather panel. Diagnostic and preview ID lists are identical and ordered the
same way. None of these pages contains duplicate IDs. Shared navigation, settings,
assets, map controls, and other layout markup are duplicated today.

The complete HTML diffs show only the variants above and whitespace near the
sequence toolbar and allocation comment. Keep weather panel text, field defaults,
inline styles, and control ordering unchanged during consolidation. The diagnostic
page intentionally retains the preview header even though its browser title differs.

## Selected approach: generate static pages from one template

The implementation uses one source template for common navigation, settings, assets, and layout.
A separate literal weather-panel fragment and a small explicit page-variant
manifest define the differences. A deterministic build-time generator writes the
three existing HTML paths. Commit generated pages so existing static hosting, local startup, and Docker
packaging continue to work without a Node runtime or server-side templating.

Source locations:

- `frontend/templates/planner.html`: common shell, stylesheets, shared script list,
  and slots for title, header, Env extension, and trailing scripts.
- `frontend/templates/weather-panel.html`: existing weather section verbatim.
- `frontend/page-variants.json`: output path, browser title, visible header,
  runtime capabilities and ordered extension scripts for each page.
- `scripts/build_page_shell.mjs`: generation and read-only `--check` modes using
  Node's standard library; no new template dependency.

The generated HTML remains under `webapp/`; template inputs stay outside the
public static directory. Preserve whitespace where practical, use explicit unique
slot markers, reject missing/unknown slots, and restrict outputs to the three
known paths. Do not replace values using broad HTML string substitutions.

### Editing workflow

Edit `frontend/templates/planner.html` for shared markup, the weather fragment for
weather-panel markup, or `frontend/page-variants.json` for page-specific titles and
ordered extension scripts. Do not edit generated HTML directly.

From the repository root, with Node 22 or later:

```sh
make build-pages
make check-pages
make check-frontend
make test-infrastructure
```

Commit template/manifest changes and generated output changes together.
`check-pages` is read-only and fails on stale or missing output; it never repairs
files silently. The CI frontend-syntax job runs this check before syntax checking.
The infrastructure unit suite also checks freshness. Generation/checking needs
no npm packages, server, credentials, or network. Browser checks require the
existing npm dependencies and installed Chromium.


Why this approach fits the current infrastructure:

- `server/main.py` serves `/static` from `webapp/` and reads `index.html` for `/`.
- `Dockerfile` copies `webapp/` into the image; it does not run a frontend build.
- Browser smoke tests serve actual files from `webapp/` with mocked services.
- Existing infrastructure testing already requires Node 22; production does not.

## Script and initialization contract

The shared script order is below. Extension entrypoint query strings were bumped
for the new registration contract to avoid cached self-starting scripts:

1. Leaflet, then Cesium.
2. `latlng_utils.js`, then `voice.js`.
3. `preferences.js`, `http_client.js`, `tab_controls.js`, `modal_control.js`,
   `dom_helpers.js`, `debug_output.js`, `llm_settings.js`, then
   `voice_controls.js`, `trace_view.js`, `chat_view.js`, `fleet_drawers.js`, then
   `memory_view.js`, then `view_preferences.js` (inert factories).
   `frontend_ui.js` follows these factories and precedes the application.
4. `isr.js`, then `cesium_view.js`.
5. Page capability JSON and `page_capabilities.js` (registration API).
6. Weather variants only: `weather_checkpoints.js`, then `weather_preview.js`.
7. Diagnostic only: `_weather_diagnostics/runner.js`.
8. `page_start.js` reads the declaration and initializes selected extensions.

Current scripts execute as classic scripts after the body markup. Do not add
`async`, `defer`, dynamic script loading, or module conversion in the template move.
The weather preview registers a one-time window-load handler and a recurring
follow timer; its initialization also configures the checkpoint controller.
Preserve that ordering without changing the controller's domain behavior.

The manifest declares two boolean runtime capabilities: `weatherPreview` and
`weatherDiagnostics`. Standard disables both, preview enables only weatherPreview,
and diagnostic enables both. The weather panel is generated from weatherPreview,
so markup and startup share one declaration. Diagnostic requires preview.

`page_capabilities.js` exposes `window.PageCapabilities.register(name, initializer)`
and `initialize(capabilities)`. Extension scripts register synchronous, zero-argument
initializers instead of starting on evaluation. `page_start.js` parses the generated
JSON declaration and starts preview before diagnostics, synchronously before window
load. The existing preview load handler and follow timer are installed at this point.
Disabled extensions never execute their initializers, even if their scripts register.
Missing selected registrations or the checkpoint controller fail explicitly before
any initializer runs; malformed declarations and duplicate registrations also fail.
The read-only `capabilities` snapshot is frozen. Initialization is once per page;
repeated startup throws to prevent duplicate listeners, timers, and wrappers. There
is no teardown/retry contract; an initializer error propagates and requires reload.

This contract selects page extensions only. Existing weather controller callbacks,
operational hooks, and presence checks in the core remain unchanged. It does not
provide a generic extension event bus or migrate runtime wrappers. Reverting this
change requires restoring the template, generator, manifest, generated pages and
extension entrypoints together.

## Runtime wrapper inventory

The [runtime wrapper inventory](RUNTIME_WRAPPER_INVENTORY.md) completes the next
Phase 5 review: 11 capture-and-replace sites across preview and diagnostics. It
records behavior, ownership, initialization order, and migration boundaries.
The two general UI wrappers, `cesiumView.init` and `cesiumView.render`, now use
the explicit lifecycle interface below. Nine mixed, operational, and diagnostic
wrappers remain unchanged.

The inventory identifies all existing Cesium callers. The original shell tests
continue to avoid 3D; dedicated lifecycle browser checks now open and reopen 3D
using a neutral engine double.

## Implementation and validation sequence

1. Extract template, panel fragment, and variant manifest from current pages.
2. Generate all three outputs and compare them with the current pages. Account
   for every difference; no control labels, IDs, defaults, or script order changes.
3. Test deterministic generation, stale-output detection, and variant invariants.
4. Run frontend syntax and the offline infrastructure suite against generated
   standard/preview pages. Add structural coverage for the diagnostic variant;
   do not run its operational runner as a general infrastructure smoke test.
5. Add generation freshness to CI and document the editing workflow.
6. Address general UI extension interfaces separately, preserving operational
   wrappers and keeping weather restricted to the existing weather pages.

Baseline: Phase 4's hosted frontend syntax and general UI infrastructure jobs
passed in PR #37. Overall CI remains failing on two Python tests and four mypy
errors; this shell work must not conceal or weaken those checks.

See [the refactor checklist](../REPOSITORY_INFRASTRUCTURE_REFACTOR_CHECKLIST.md)
and [frontend infrastructure contracts](FRONTEND_INFRASTRUCTURE.md).

## Validation of the initial extraction

The standard page is byte-for-byte unchanged. Preview and diagnostic outputs
only normalize whitespace around the sequence-toolbar and allocation comments;
labels, values, IDs, styles, URLs, and scripts are preserved. No runtime JavaScript
or server code changed.

Local validation: 30 Node checks and 22 browser checks passed. The five new
page-shell checks cover output freshness, deterministic rendering, variant IDs
and script order, rejected slots/manifest values, escaped titles, and stale/missing
output detection without writes. Generation repairs stale outputs in a temporary
test workspace. All 23 tracked frontend scripts passed syntax checks; the new
generator was parsed separately. The new CI freshness step awaits hosted execution.

## Validation of explicit capability startup (2026-10-21)

All 34 infrastructure unit checks and 22 Chromium checks passed locally. Coverage
includes declarations for all three generated pages, disabled real entrypoints
remaining inert, dependency failures before initialization, initialization order,
and duplicate startup rejection. Standard/preview browser checks verify the
runtime declarations and preview availability alongside existing UI behavior.
Diagnostic startup selection is covered structurally and with mocked initializers;
its operational runner is not executed by the infrastructure browser suite.
The existing offline weather acceptance and diagnostic replay regressions passed
after their harnesses were updated to initialize explicitly. The separate jsdom
Fit Mission harness was adapted but not run in this checkout.

Page freshness, tracked frontend syntax, the two new runtime files' syntax,
generator syntax, and diff whitespace checks passed. Hosted CI has not been run
for this change. No commit, merge, or deployment was performed.

## Cesium presentation lifecycle contract (2026-10-21)

`cesiumView.subscribeLifecycle({ onInit, onRender })` registers one or both
synchronous callbacks and returns an idempotent unsubscribe function. Invalid
callback types and empty subscriptions throw `TypeError`. Registration does not
replay earlier events. Each subscription is independent, even when callbacks are
shared. This is an observer interface; callback return values are ignored.

- `onInit(viewer)` runs after successful initialization, before the returned
  promise resolves to its caller. It also runs when init reuses a viewer, or with
  `null` when the Cesium engine is unavailable, matching the previous wrapper.
  Constructor failures reject initialization without notification.
- `onRender(viewer, options)` runs synchronously after the base scene render,
  including its camera-preserving early return. It receives the caller's options
  object, or the default empty object. Render still returns `undefined`. A render
  with no viewer notifies with `null`, matching the previous wrapper's invocation
  of the guarded weather renderer. Base render failures suppress notification.
- Callbacks run in subscription order. A dispatch snapshots registrations;
  additions wait until the next dispatch and removals take effect immediately.
  Observer exceptions propagate through render, or reject the init promise, and
  stop that notification pass. Callbacks are not awaited or isolated.
- Subscriptions belong to the page and survive viewer destroy/recreate. Destroy
  still destroys only the viewer and clears its existing scene state. Consumers
  that need shorter ownership retain and invoke the returned unsubscribe function.
  This does not add viewer creation cancellation or alter concurrent init behavior.

Preview registers one page-lifetime subscription to capture the initialized
viewer and draw its overlay after base rendering. It keeps the existing destroyed
viewer guard and direct `draw()` paths. Only its two Cesium wrappers are removed;
core callers, weather selection/calculations, and operational hooks are unchanged.
Standard has no weather subscription. Preview/diagnostic retain the weather panel
and capability flags. Cesium and preview asset query strings were bumped together.

Validation: 40 infrastructure unit checks and 24 Chromium checks passed locally.
Six new unit checks cover completion and error ordering, both render camera paths,
subscription mutation/disposal, unavailable engine, and reopening. Two new browser
checks use the real page controls, real application scripts, and a neutral Cesium
engine double on standard/preview pages. They check unchanged public method
identities, base-before-overlay ordering, weather viewer capture after reopening,
and unsubscribe behavior. They do not validate WebGL output, live terrain, or
weather geometry. The existing shell tests still use an empty CDN engine stub.

Offline weather acceptance (11 scenarios), diagnostic replay, 3D visibility, and
3D marker/lifecycle regressions passed. Their VM/source-extraction adapters were
updated only where the explicit lifecycle contract required it. The separate
jsdom Fit Mission harness was adapted and syntax-checked, but not executed.
Frontend syntax (25 tracked scripts), generator/test syntax, generated-page
freshness, and diff whitespace checks passed. A source comparison confirmed the
base scene-rendering body is unchanged. Hosted CI and full Python checks were not
run for this change; previously recorded baseline blockers remain separate.

Rollback requires reverting the lifecycle API, preview subscription, test adapter
changes, versioned template/manifest URLs, and regenerated pages together. No
commit, merge, or deployment was performed.

## Settings extraction script integration (2026-10-24)

The common shell now loads `llm_settings.js` before `isr.js`, after the existing
infrastructure helpers. `isr.js` owns controller construction and DOM-ready startup;
page capabilities and weather/diagnostic initialization remain unchanged. All
three generated outputs were regenerated. Relative order of every previously
loaded script is preserved. The module and changed main-script query labels use
`20261024-settings`. Local freshness/loading checks and 26 mocked Chromium checks
passed; this is not a hosted deployment verification.

## Debug writer script integration (2026-10-24)

The shared shell also loads the inert `debug_output.js` factory before `isr.js`.
The writer and main-script query labels use `20261024-debug`; settings retains its
own version label. Existing relative script ordering and page capabilities remain
unchanged. Freshness/loading checks and 28 mocked Chromium checks passed locally.

## Voice controls script integration (2026-10-24)

`voice_controls.js` now loads before `isr.js`, after the existing engine and
infrastructure scripts. Its factory owns no startup; the main script creates one
controller and retains the previous startup helpers. Both changed asset labels
use `20261024-voice-controls`. All three outputs were regenerated with unchanged
relative ordering/capabilities/markup. Freshness/order checks and 30 mocked
Chromium checks pass locally; live engines and operational diagnostics were not
run.

## Trace view script integration (2026-10-24)

`trace_view.js` loads before `isr.js`; only the main script composes the view and
retains trace state/callers. Both changed asset labels use `20261024-trace-view`.
All three outputs were regenerated with unchanged markup, capabilities, and
relative ordering of existing scripts. Loading/freshness checks pass. Dedicated
trace UI cases use neutral records; the extraction checklist records the separate
map/3D browser timing finding and final verification disposition.


## Map resize lifecycle follow-up (2026-10-24)

The main-script label is now `20261024-map-resize` for the focused Leaflet timer
cleanup. Module labels and script order are unchanged. The shared template and all
three generated pages were updated together; no markup/capabilities changed.


## Chat view script integration (2026-10-24)

`chat_view.js` loads before `isr.js`; both changed labels use
`20261024-chat-view`. Existing module labels, relative ordering, startup,
capabilities, and markup are preserved. All three pages are regenerated from the
shared template. The main script owns composition; the view has no startup hook.


## Drawer controller script integration (2026-10-24)

`fleet_drawers.js` loads before `isr.js`; both changed labels use
`20261024-fleet-drawers`. Existing module order, capabilities, markup, labels, and
startup are preserved. All three generated pages come from the shared template;
the main script owns composition and existing listener startup.


## Shared HTML text utility integration (2026-10-24)

The existing `dom_helpers.js` and `isr.js` labels advance to `20261024-html-text`.
There is no new script or startup path. Relative script order, capabilities,
markup, module labels for other scripts, and script counts are unchanged. All
three generated variants are refreshed from the shared template.


## Memory-list view script integration (2026-10-24)

`memory_view.js` loads before `isr.js`; both changed labels use
`20261024-memory-view`. Existing script ordering, capabilities, markup and startup
remain unchanged. All three generated variants are refreshed from the shared
template. The view has no independent startup; composition remains in the main script.


## Viewport preference script integration (2026-10-24)

`view_preferences.js` loads before `isr.js`; both changed labels use
`20261024-view-preferences`. Other script labels, relative order, capabilities,
markup and startup are preserved. All three pages are regenerated from the shared
template. The main script composes the store after its existing viewport constants;
the module has no independent startup or eager browser-storage access.


## General UI composition integration (2026-10-26)

`frontend_ui.js` composes seven existing general UI factories without startup.
It loads after its dependencies and before `isr.js`; both changed asset labels
use `20261026-composition`. Other labels, relative script order, capabilities,
markup and extension/DOM-ready/load timing are unchanged. All three generated
variants are refreshed from the shared template and have loading/order coverage.
Local infrastructure results: 92 unit / 46 Chromium checks passed. Contracts and
limits are in [General UI Composition](FRONTEND_UI_COMPOSITION.md).
