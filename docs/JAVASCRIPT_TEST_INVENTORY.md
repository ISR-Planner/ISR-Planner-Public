# JavaScript Checks and Infrastructure Testing

The `tests/e2e` directory contains several kinds of checks, not only end-to-end
browser tests. This inventory classifies its 34 JavaScript files by execution
method, plus the syntax checker and new shared-shell browser suite. Existing
domain-specific checks are inventoried here, not modified or executed as part
of this infrastructure work.

## Repeatable general infrastructure commands

Use Node 22 or later from the repository root. Install the locked dependencies
and matching browser once:

```sh
npm ci --ignore-scripts
npm run install:browser
```

On a Linux CI runner, install browser system dependencies as well:

```sh
npx --no-install playwright install --with-deps chromium
```

Then run:

```sh
make check-frontend
make test-infrastructure
```

The first command parses tracked frontend JavaScript without executing it. It
does not cover inline HTML scripts or browser behavior. The second runs the
infrastructure Node unit suite and real Chromium tests against the actual standard and preview HTML, local CSS,
and application scripts. It needs no Python server, credentials, private files,
or internet access during test execution. Dependency/browser installation does
need internet unless the packages and browser are already cached.

`package.json` and `package-lock.json` pin Playwright and Leaflet. This package
is test tooling, not a frontend production build system; it does not change how
Railway serves the application. The package has no `type: module` setting, so
existing CommonJS tests continue to use `require` normally.

## Shared shell browser coverage

Entry point: `tests/infrastructure/shared-shell.spec.mjs`.
Configuration: `tests/infrastructure/playwright.config.mjs`.
Test adapters: `tests/infrastructure/shell-fixture.mjs`.

The shared-shell cases cover both `webapp/index.html` and
`webapp/weather_preview.html`. Coverage includes:

1. Page load, canvas visibility, intended weather-panel presence, and all seven
   tab buttons activating their corresponding pane exclusively.
2. Settings open/close, model selection, save, and persistence after reload.
3. Explicit service error display followed by successful recovery on retry.
4. Transport failure display while the modal remains usable.
5. Local-provider host/model persistence and mocked connection payload.
6. Stale selection fallback and removal of legacy credential entries only.
7. Blocked-storage handling that reports a save failure and leaves the modal open.

The harness intercepts every browser request under a synthetic `shell.test`
origin. HTML, JavaScript, and CSS are served from local files. Startup APIs return
empty data and synthetic provider choices. Leaflet is served from the exact local
package version used by the page, and map tiles are blank fixture images. The
unused external Cesium engine and stylesheet are replaced with empty responses;
no 3D view is opened. Unknown URLs and unexpected API calls fail the test rather
than reaching a live service. Uncaught browser errors also fail the test.

No application function is replaced to force the UI assertions to pass. The
tests exercise real controls in Chromium, but do not verify map imagery, 3D,
speech, domain operations, or backend integration. The preview page's normal
startup behavior is preserved, including its empty-environment prompt.

Browser storage is isolated per test. Only a neutral initial viewport is seeded;
settings are changed through UI controls. No real API key is provided. Failures
retain a screenshot and trace under ignored `test-results/infrastructure/`.

Local result: twenty-two checks passed in Chromium and thirty Node checks passed. CI is configured to run the same
command; a successful hosted CI run has not yet been observed. Existing Python
baseline blockers remain in [Baseline Blockers](Baseline-Blockers.md).

## Execution types

- **Syntax:** parses source without running it.
- **Isolated unit:** evaluates functions or modules with synthetic state; may use
  fake timers or small input/engine stubs, but does not exercise a browser DOM.
- **Simulated DOM:** uses hand-built DOM/canvas objects or jsdom. This does not
  prove real-browser layout, event, or rendering behavior.
- **Real browser:** executes in Chromium or a manually opened browser.
- **Integration:** combines multiple components. It can be offline; a mocked
  `fetch` call is not evidence that the test needs a live network.

## Dependency profiles

| Profile | Browser | Server | Credentials | Internet during execution | Other requirements |
| --- | --- | --- | --- | --- | --- |
| N | No | No | No | No | Node, repository files, built-in modules |
| D | No real browser | No | No | No | Node, jsdom 26 and Leaflet 1.9.4; legacy header documents installation |
| P | No | No HTTP server | No | No | Node and the Python environment required by its subprocess |
| B | Chromium | Existing application URL | Not intrinsically required for browser assertions; server configuration may require them | May load CDNs, map tiles, and startup APIs | Playwright and a supplied JSON fixture; current scripts do not provide complete network isolation |
| M | Manually opened browser | Running application | Depends on actions and server configuration | Depends on page and services | Browser-console diagnostic, not an automated runner |
| S | Chromium | No | No | No | Locked npm dependencies, installed Chromium; fully mocked browser requests |

Installation is separate from execution: npm packages, browsers, and Python
dependencies can require network access when not cached. B/M profiles are not
guaranteed offline and are not included in the general infrastructure CI job.

## Existing checks by file

Paths below are relative to `tests/e2e/`. Classification describes the existing
implementation rather than assigning confidence based on the filename.

| File | Type | Profile and qualifications |
| --- | --- | --- |
| `test_agent_provider_request.js` | Simulated DOM/request adapter | N; request handling is mocked |
| `test_altitude_phase_a.js` | Isolated unit with simulated UI inputs | N |
| `test_animation_comparison.js` | Manual browser diagnostic | M; paste/run in an already loaded browser |
| `test_animation_timing.js` | Isolated unit with fake timers/state | N |
| `test_asset_tab.js` | Isolated unit | N; extracted helpers and synthetic values |
| `test_chase_cam_smoothing.js` | Isolated unit with input/engine stubs | N; not real Cesium rendering |
| `test_checkpoint_playback_timeline.js` | Isolated unit | N |
| `test_env_tab.js` | Simulated DOM | N; small fake elements and generated markup |
| `test_flight_position.js` | Isolated unit | N |
| `test_flight_projection.js` | Isolated unit | N |
| `test_landed_drone.js` | Isolated unit | N |
| `test_leaflet_fit_mission.js` | Simulated DOM integration | D; real Leaflet in jsdom, mocked dimensions and no tile network; optional `--baseline` reads Git |
| `test_lost_drones.js` | Isolated unit | N |
| `test_map_coordinates.js` | Simulated DOM integration | N; mocked elements and map adapter |
| `test_mission_sequence.js` | Isolated unit | N |
| `test_route_visibility.js` | Simulated canvas integration | N |
| `test_route_visibility_3d.js` | Simulated rendering integration | N; VM/engine adapters, not a real browser |
| `test_sam_boundary_altitude.js` | Isolated unit with UI stubs | N |
| `test_segment_3d_replay.js` | Real browser integration | B; URL and JSON fixture arguments |
| `test_segment_loiter.js` | Isolated unit; optional real browser integration | N by default; B with `--browser` and supplied URL/JSON |
| `test_segment_replay_targets.js` | Real browser integration | B; URL and JSON fixture arguments |
| `test_segment_target_lists.js` | Real browser integration | B; URL and JSON fixture arguments |
| `test_time_sync_markers_3d.js` | Simulated DOM/engine integration | N; mocked canvas and Cesium objects |
| `test_weather_accept.js` | Simulated DOM integration | N; shared offline harness and checked-in capture |
| `test_weather_checkpoint_controller.js` | Simulated DOM integration | N; network adapter mocked |
| `test_weather_clock_units.js` | Isolated unit | N |
| `test_weather_demo_paths.js` | Simulated DOM integration | N; imports shared harness; optional external demo file argument |
| `test_weather_detour_checkpoint.js` | Offline Python/JavaScript integration | P; `ISR_TEST_PYTHON` overrides its default `venv/bin/python`; no HTTP server |
| `test_weather_diagnostic_replay.js` | Simulated DOM/request integration | N; checked-in capture and mocked requests |
| `test_weather_geometry.js` | Simulated DOM integration | N; imports the acceptance harness and two checked-in captures |
| `test_weather_lifecycle.js` | Simulated DOM integration | N |
| `test_weather_remaining_routes.js` | Simulated DOM/request integration | N |
| `test_weather_slider_range.js` | Simulated UI elements | N |
| `test_weather_solve_gate.js` | Simulated UI elements | N |

Additional entry points:

| Entry point | Type | Requirements |
| --- | --- | --- |
| `scripts/check_frontend_syntax.mjs` | Syntax | Node 22+, Git; no browser/server/credentials/network |
| `tests/infrastructure/shared-shell.spec.mjs` | Real browser shell integration | S; twenty-two tests, both pages |
| `tests/infrastructure/preferences.test.cjs` | Isolated unit and HTML loading-order contract | N; nine checks; imports the public adapter directly |

The legacy Node tests often extract functions from source text using string
searches and evaluate them in `node:vm`. Moving or reformatting a function can
break the harness even when behavior is unchanged. As general infrastructure
modules are extracted, prefer importing their public interfaces in new unit tests.
Do not reinterpret simulated-DOM checks as browser coverage.

## CI and local operation

The `frontend-syntax` CI job checks parsing. The separate `infrastructure-browser`
job installs locked npm dependencies, installs Chromium with Linux system
dependencies, and runs `make test-infrastructure`. It uploads failure diagnostics
for seven days. Neither job needs application secrets or a Python server.

Workflow triggers remain pull requests and pushes to `main`/`master`; a push to
`Refactor-2` alone does not trigger this workflow unless it is part of a PR event.
Python jobs remain independent and their existing failures are not suppressed.

On sandboxed macOS hosts, Chromium may need permission to launch outside the
execution sandbox. A launch permission failure is an environment failure, not
an application regression. The successful local run used an approved launch.

For a focused run, use `npm run test:infra:unit` (no browser or npm dependencies
needed by the test code) or `npm run test:infra:browser`. The combined command
runs the unit checks first and stops if they fail.

Phase 4 also adds `http_client.test.cjs` (five isolated request-contract checks)
and `tab_controls.test.cjs` (four simulated-DOM lifecycle and selection checks).
Both run under Node without a browser, server, credentials, or internet.

`modal_control.test.cjs` adds three simulated-DOM visibility/lifecycle checks.
The browser suite also verifies backdrop versus child clicks and modal remounting
on both pages. These use the same offline infrastructure command.

Settings form lifecycle checks exercise all five handlers on both pages, including
repeat setup, disposal, detached elements, and binding replacement elements.
Service methods are replaced with counters for this lifecycle check.

`dom_helpers.test.cjs` adds four isolated checks for fresh ID lookup, text
conversion, missing elements, and error propagation. Browser tests exercise the
compatibility wrappers with neutral DOM elements on both pages.

Phase 5 adds five `page_shell.test.cjs` checks for static generation and freshness.
Run `make build-pages` after editing `frontend/` inputs, and `make check-pages` to
verify committed outputs without writing. CI runs the latter before frontend
syntax checks. These commands require Node only; they execute no application code.

### Phase 5 Cesium lifecycle coverage

`tests/infrastructure/cesium_lifecycle.test.cjs` adds six offline unit checks
against the full Cesium view module with a neutral engine double.
`cesium-lifecycle.spec.mjs` adds two Chromium checks using the standard/preview
page controls with that double. They verify initialization and render ordering,
public method identity, observer cleanup, and reopening without a live service,
WebGL renderer, weather data, or terrain. Existing shared-shell checks still
serve an empty engine stub. The infrastructure suite now has 40 unit and 24
browser checks, locally passing for this migration; that result does not claim
hosted CI or live 3D rendering verification. See the
[lifecycle contract](architecture/SHARED_PAGE_SHELL.md#cesium-presentation-lifecycle-contract-2026-10-21).

### Settings controller extraction coverage (2026-10-24)

`tests/infrastructure/llm_settings.test.cjs` adds ten checks against the actual
factory without loading the entire application: inert/independent construction,
init ordering, serialization, save results, missing/unconfigured selection,
discovery failure/restoration, and both ready-state branches of the application
composition. Shared-shell Chromium checks add local discovery error/recovery on
standard and preview. The provider-request VM adapter now loads the shipped
settings module and composition binding; existing request assertions are retained.
The infrastructure suite passed locally with 50 unit and 26 browser checks, plus
the standalone provider-request check. No live-provider or hosted CI result is
claimed. See the [extraction checklist](FRONTEND_EXTRACTION_CHECKLIST.md).

### Debug text writer extraction coverage (2026-10-24)

`debug_output.test.cjs` adds three isolated contract checks. Shared-shell Chromium
checks exercise the actual `appendDebugLine` delegate with neutral text, current
locale-formatting invocation, replacement/missing elements, and literal output.
The current infrastructure suite passes locally with 53 unit / 28 Chromium checks;
the provider-request assertions also pass. All three generated pages have writer
loading/order coverage; diagnostic operational workflows are not executed. The
writer's caller messages and domain logic are unchanged.

### Voice control extraction coverage (2026-10-24)

`voice_controls.test.cjs` adds eight isolated fake-engine checks for wiring,
labels/focus, dictation, engine discovery/fallback, reply ownership, and lifecycle.
`voice-controls.spec.mjs` adds two real-page checks (standard/preview) with the
engine script replaced by fakes and the engine list mocked. They exercise
keyboard activation, controls, replies, and disposal/remount without microphone,
audio, model downloads, or agent requests. All generated variants have module
loading/order coverage. The infrastructure suite passed locally with 61 unit /
30 Chromium checks; provider-request assertions also passed. This does not claim
real-engine, live-service, full-suite, or hosted CI verification.

### Trace display extraction coverage (2026-10-24)

`trace_view.test.cjs` adds four isolated renderer contracts covering inert/lazy
construction, empty display, ordering/escaping/truncation/input ownership, and
failure propagation. `trace-view.spec.mjs` adds two standard/preview UI checks with
neutral records and real clear-confirmation controls. No agent requests or
operational interpretation are involved. Unit count is 65; the final full
Chromium rerun passed all 32 checks. The extraction checklist records browser results and an observed timing-sensitive Leaflet
failure in the existing weather/3D case; do not silently treat the first run as
passing or conflate these UI checks with a full operational test suite.


### Leaflet timer regression coverage (2026-10-24)

`leaflet-lifecycle.spec.mjs` adds two standard/preview checks with a controlled
browser clock and real local Leaflet. They reproduce removed-map resize failure
before the fix, then verify canceled delayed work, replacement ownership, repeated
init/teardown, and a single live resize at 200 ms. Local results after the focused
timer fix: **65 unit / 34 Chromium checks passed**, plus provider-request checks.
These neutral UI checks make no operational or live-service verification claim.


### Chat presentation extraction coverage (2026-10-24)

`chat_view.test.cjs` adds four isolated contracts for inert construction/detached
cards, caller HTML and scrolling/lazy history, removal, and dependency failures.
`chat-view.spec.mjs` adds two standard/preview browser cases for actual escaped
text, locale formatting, labels/hidden replay, system HTML, appended order/scroll,
returned IDs, missing/replacement history, and removal. Existing welcome messages
are preserved. Module loading/order is checked on all three generated variants.
These checks use neutral content without sending agent requests. Dated results
and limitations are recorded in the frontend extraction checklist.

Latest local extraction results: **69 unit / 36 Chromium checks passed**, plus
provider-request assertions. No live-service or operational-suite result is claimed.


### Drawer disclosure extraction coverage (2026-10-24)

`fleet_drawers.test.cjs` adds five isolated contracts covering inert construction,
independent disclosure/ARIA/arrow/class/collapse presentation, repeatable mounting,
owned listener disposal, lazy direct updates, replacement remount, missing/partial
markup, and propagated failures. `fleet-drawers.spec.mjs` adds standard/preview
checks using native keyboard activation and disabled buttons, multiple open rows,
Collapse all, repeated mounting/disposal, and replacement controls. These neutral
DOM checks do not modify configuration or send requests. All generated variants
have module loading/order coverage. Results are recorded in extraction 7 of the
frontend extraction checklist.

Latest local extraction results: **74 unit / 38 Chromium checks passed**, plus
provider-request assertions. Browser disclosure coverage uses keyboard activation
and retains the existing edit gate; the first new-test pointer-click timeouts and
correction are documented in extraction 7. No full operational-suite result is claimed.


### Shared HTML text escaping coverage (2026-10-24)

Two additions to `dom_helpers.test.cjs` check lazy detached-node creation, verbatim
DOM assignment/delegated conversion, and element/assignment/serialization failures.
`html-text-escaping.spec.mjs` adds standard/preview real-browser coverage of the
actual global delegate and DOM interface: escaping, unchanged quotes, nullish/
primitive/custom conversion, Unicode/newlines, detached elements, and propagated
conversion failures. No agent requests or domain fixtures are involved. Existing
chat and trace browser tests continue to cover their injected escaping callers.
Dated results are recorded in extraction 8 of the frontend extraction checklist.

Latest local extraction results: **76 unit / 40 Chromium checks passed**, plus
provider-request assertions. No operational-suite or live-service result is claimed.


### Memory-list display extraction coverage (2026-10-24)

`memory_view.test.cjs` adds four isolated contracts for inert/lazy/missing/replacement
lists, empty presentation, ordered records/content escaping/input ownership and
propagated failures. `memory-view.spec.mjs` adds standard/preview real-DOM checks
for empty/populated content, escaping, ID/category/order, unchanged count/panel
behavior, missing/replacement lists, and native keyboard dispatch to the existing
delete entrypoint via a temporary neutral callback. No memory writes or agent
requests are issued. All generated variants have module loading/order coverage.
Dated results and limitations are recorded in extraction 9 of the frontend checklist.

Latest local extraction results: **80 unit / 42 Chromium checks passed**, plus
provider-request assertions. No operational-suite or live-service result is claimed.


### Viewport preference extraction coverage (2026-10-24)

`view_preferences.test.cjs` adds six contracts for inert/lazy construction,
independent instances, exact JSON/rounding/deduplication, failed-write suppression,
key-format errors, validation/TTL/future dates/defaults, normalization, and read
failures. `view-preferences.spec.mjs` adds standard/preview wrapper checks with
neutral views, actual storage, denied writes/recovery semantics, reload restoration,
and unrelated preference preservation. The shared fixture now seeds `isrLastView`
only if absent so reload does not overwrite the record under test. Other browser
fixtures and existing scenario-free checks remain in the suite.

`test_map_coordinates.js` now loads the actual new module and production
composition before its sliced wrappers; all twelve pre-existing assertions remain.
These caller regressions are separate from the infrastructure counts and do not
constitute a live-service or operational-suite claim. Dated results are recorded
in extraction 10 of the frontend checklist.

Latest local extraction results: **86 unit / 44 Chromium checks passed**, plus
provider-request and 12 existing coordinate regressions. This is local verification,
not a live-service, hosted CI or full operational-suite result.


### General UI composition architecture coverage (2026-10-26)

`frontend_ui.test.cjs` adds six isolated contracts: construction order and exact
instance/adapter identity, lazy fixed-ID lookups/errors, factory failure propagation,
inert composition using real factories, and actual main-script lexical/lazy behavior
with loading/complete documents. The last two cases cover construction only;
existing settings tests retain ready-state initialization coverage.

`frontend-ui.spec.mjs` adds standard/preview browser cases observing one composition
and one construction of each component, instance consumption by existing delegates,
return/argument identity and lexical bindings. Observation and temporary stubs are
test-only; no runtime instrumentation or provider/memory write is introduced.
All three variants have dependency/composition/main-script order coverage.

Fresh baseline: 86 unit / 44 Chromium checks. After the slice: **92 unit / 46
Chromium checks passed**, plus provider-request, syntax and page freshness checks.
No full Python, real-engine, live-provider or hosted CI result is claimed. Contracts,
source scope and limitations are in [General UI Composition](architecture/FRONTEND_UI_COMPOSITION.md).


### General UI lifecycle ownership characterization (2026-10-26)

Stage C adds five isolated cases to existing suites: tabs/modal preserve foreign
listeners, tab replacement remount does not reactivate detached controls, pending
settings init can mount after listener disposal, model discovery retains its
captured selector, and voice cleanup leaves handed-out STT callbacks and separately
owned reply handlers intact. Existing drawer/voice/settings/modal/tab cases continue
to cover narrow repeated mount/disposal/remount and display preservation.

`ui-lifecycle.spec.mjs` adds standard/preview real-browser checks for native keyboard
dispatch, foreign listener preservation, current modal visibility, and settings
replacement remount. Connection methods are temporarily stubbed with neutral
counters, so no provider or memory write occurs. Runtime modules and script/page
structure are unchanged. Ownership and async limits are documented in
[General UI Lifecycle](architecture/FRONTEND_UI_LIFECYCLE.md).

Latest stage C local results: **97 unit / 48 Chromium checks passed**, retries
disabled, with a 92/46 fresh baseline. Provider-request baseline, syntax and page
freshness passed. This is test/documentation characterization of existing runtime
behavior, not new async shutdown or full application remount support.


### General presentation adapter characterization (2026-10-26)

Stage D adds one case each to `trace_view.test.cjs` and `memory_view.test.cjs` for
borrowed mutable records, snapshot timing and explicit rerender refresh without
input mutation. Existing frozen-input, format/error, missing/replacement-node,
legacy HTML and inline-action contracts remain intact.

`presentation-adapters.spec.mjs` adds standard/preview-hosted checks that compose
real modules against an isolated HTML document. Supplied neutral records and
clock/ID callbacks drive rendering; voice/transport callbacks throw if accessed.
Assertions verify refreshed display values, snapshot ownership, escaped/literal/
legacy HTML distinctions, detached cards and unchanged host application DOM.
No application state adapter, memory action or agent request is introduced.
[Presentation Adapters](architecture/FRONTEND_PRESENTATION_ADAPTERS.md) records the
reviewed interfaces and limits. Runtime code and page/script structure are unchanged.

Latest stage D local results: **99 unit / 50 Chromium checks passed**, retries
disabled, from a 97/48 fresh baseline. Provider-request baseline, syntax and page
freshness passed. This characterizes existing interfaces; it does not change
formatting/HTML policy or confer state/transport authority on a view.


### Architecture iteration closeout (2026-10-26)

[Stage E closeout](architecture/FRONTEND_ARCHITECTURE_CLOSEOUT.md) records the
selected architecture goals, source-scope audit and retained work. Latest recorded
suite results remain stage D's **99 unit / 50 Chromium checks passed**; stage E is
documentation-only and does not claim a new suite run. Source/consistency, current
frontend syntax, generated-page freshness, documentation links and diff checks
are verified for closeout. Existing tests and runtime code are unchanged by stage E.
