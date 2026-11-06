# Weather planning preview

Open `/static/weather_preview.html` to use the isolated weather preview. The normal planner remains at `/`.

**Current Agent testing (October 13):** use [Railway](https://isr-planner-production.up.railway.app/static/weather_preview.html), deployed with runtime `26b394f`. Kamal deferred the home localhost Agent's invalid credential repair. See the [current handoff](session-notes/HANDOFF_2026-10-12_weather-demo-paths.md#current-test-environment--october-13) for the Agent setup and exact server distinction. The ordinary Run Planner solver does not need an LLM key.

The [weather demo pathways handoff](session-notes/HANDOFF_2026-10-12_weather-demo-paths.md) covers the current presentation workflow. Both pathways use this preview page:

**Before importing:** It is advisable to hard refresh before every fresh mission/environment import or **Load demo**, on both localhost and Railway. In Chrome, use **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux). This reloads the latest scripts and starts a fresh page; then import the file to initialize the mission and help prevent stale state from a previous run carrying over.

| Pathway | Load and run |
| --- | --- |
| Recorded demo | **Hard refresh → Load demo** → select an `isr_weather_demo_v1` JSON → **Run Planner → Accept → Animate**. The file restores the mission, recorded NOAA capture, original dates, map bounds, and playback speed. No live weather request replaces that capture during checkpoints. |
| Live NOAA | **Hard refresh** → import a plain environment JSON → select **Live NOAA** → **Fit mission → Get forecast → Run Planner → Accept → Animate**. Import initializes a current 16-hour mission window; adjust it and Update forecast before solving if needed. |

**Save demo** packages the original mission and captured weather for another computer. It saves a scenario to re-solve, not a video or preaccepted result. The source selector clears the old accepted route when switching pathways. At a weather checkpoint, review the revised draft and click **Accept → Animate**.

**Reset** now returns route, mission clock, visited/fuel state, weather dates, and checkpoint controller to the original run. Confirm the existing warning if cut history will be discarded, then Animate to replay the accepted original route. For a different scenario or a fresh import of the same one, hard refresh first, then load the demo or import the environment. Pending requests from an old run cannot overwrite the new mission.

Animate checks that loaded weather still matches the accepted route. If weather changed after acceptance, Reset restores the original replay, or importing the mission/demo again starts a new solve workflow.

The presentation files are private local artifacts in `/Users/kamalali/Downloads/ISR Weather Demo Kit/`; they are not committed or served by the website. Transfer the kit ZIP separately to the office computer. The recorded example starts October 11, 2026 at **20:24 CDT / October 12 01:24 UTC** and ends October 12 at **12:24 CDT / 17:24 UTC**. Do not replace those dates with presentation day.

Get forecast retrieves current NOAA Aviation Weather Center SIGMETs and G-AIRMET forecast slices (0, 3, 6, 9, and 12 hours) through `/api/weather/current`. The server caches successful retrievals for at most one minute to respect NOAA request limits. Each loaded snapshot records its actual retrieval time and source URLs. Retrieval failure is displayed and blocks a weather solve until a successful retry; the preview never silently substitutes the historical file. International SIGMET coverage is partial; G-AIRMET slices cover the contiguous US. Missing advisories do not indicate clear conditions. Forecast availability is limited to published times, not an arbitrary future mission window.

Selecting an earlier mission Start currently filters the latest downloaded package; the preview does not send a historical date query to NOAA. Importing a plain environment JSON does not restore the weather used in its earlier solve; a weather demo JSON explicitly includes that capture.

For repeatable local timing diagnostics, `/static/_weather_diagnostics/index.html` explicitly replays the public NOAA capture retrieved on October 11, 2026 at 20:23:37 CDT (October 12 at 01:23:37 UTC). The page identifies this as historical test data, retains the original geometries and validity times, and initializes a matching 16-hour mission window. Import a mission and select Run comparisons to test 1×, 4×, 16×, 64×, and 128×, followed by 128× with weather hidden. Timing comparisons temporarily disable weather checkpoints and restore the operator's checkbox preferences when finished or failed. Timing cases reuse the same captured weather and solved route. Manual Run Planner and checkpoint replans always call the backend for a fresh result. The private mission remains in browser memory. `node tests/e2e/test_weather_diagnostic_replay.js` verifies capture reuse, selected times, fresh manual replans, and preference restoration without browser access.

The targeted offline integration regression is `node work/diagnostics/weather_checkpoint_integration.js <mission-json-path>`. It reads the private mission in memory, calls the real backend directly, and runs the actual JS animation, pause, cut, checkpoint-request builder, and repair-candidate builder. The recorded T1/T4 expiry case is checked at 1×, 4×, 16×, 64×, 128×, 1,280×, 12,800×, and changing multipliers. It requires one C1-1 cut at 02:55 UTC, T8 retained as visited, unchanged tank capacity and correctly reduced start fuel, a fresh C1-1 route containing T1/T4, a weather-review warning, and paused review. Rendering/Chrome wall-clock performance are outside this offline regression's scope.

Before Run Planner, loaded weather adds the union of No fly polygons valid anywhere in the mission window, at every altitude, to routing. The demo applies a fixed 5 km margin and conservative convex boundaries. Other advisories have no sensing or loiter effect. Visibility filters do not disable constraints. Changing Map bounds through operator pan/zoom or changing mission times requires Update forecast before solving. Internal planner redraws do not invalidate the weather selection.

Accept verifies the complete weather snapshot captured for the draft (area, advisories, validity window, retrieval time, and avoidance policy), together with the selected mission times. Panning or zooming to inspect an existing draft does not change its captured forecast and does not block Accept. Obtaining a different forecast or changing mission times requires a new solve; any acceptance blocker is shown beside Mission Control as well as in Weather and Debug.

Weather is clipped on the sphere to the supported routing domain before projection, with additional space for the margin. Distant polygons cannot shift the flight projection centre. Returned trajectories are independently checked for weather intersections and must stay within the domain. Flights outside that domain are rejected rather than losing weather constraints.

Follow mission advances weather from mission start using the same elapsed time as playback. Aircraft speeds are km/min; the absolute weather clock converts mission minutes to seconds. Pause holds time, and moving the weather slider switches to manual inspection. Weather beyond the selected window is unavailable.

The preview starts with an empty environment. Import a mission JSON, use Fit mission to frame its airports and targets, and load weather for that area. Importing another mission clears the previous weather selection. Choose an aircraft and adjust its settings before planning.

Weather checkpoints inspect published No fly validity transitions and inspect the selected source at one-minute wall-clock intervals during flight. Live mode retrieves current NOAA; recorded mode reuses the demo capture. Changes are checked against remaining trajectories, previously weather-blocked unvisited targets, and weather detours that can now be shortened. A relevant change pauses the mission and uses the shared cut engine to preserve positions, fuel, and flown history. Released targets or detours trigger a checkpoint and a fresh draft even when playback is ahead of NOAA observations; the draft records the assessment and displays a weather-review warning. It requires explicit Accept and Animate. Expiry is not reported as confirmed clearance. Coverage loss affecting the existing remaining route holds at one checkpoint, refreshes the remaining weather window, blocks stale replanning, and retries retrieval while paused. Turning off the weather-checkpoint toggle does not disable No fly constraints.

Detour assessment preserves each remaining required target, refuel and landing stop. It checks materially shorter shortcuts within those legs against both current weather and permanent SAM constraints. Remote changes and completed detours do not trigger cuts. The remaining path and checkpoint position use the same cumulative playback distances as the aircraft, including stationary loiter intervals. If an older route lacks trustworthy waypoint indices, shortcut assessment is omitted rather than inferring which bends are required stops. `test_weather_detour_checkpoint.js` exercises the real solver, impact assessor, clock and cut engine with every target already scheduled; `test_checkpoint_playback_timeline.js` verifies positions, unvisited targets and fuel during and after loiter.


Playback multipliers are literal: 1× is one mission second per real second, and 128× is 128 mission seconds per real second. Enable **Boost** beside the speed slider and select **100×** (the default) for rates up to **12,800×**. The **10×** option preserves the earlier range up to 1,280×. The speed label always shows the effective rate. A 16-hour mission takes about 4.5 seconds at 12,800× or 45 seconds at 1,280×, excluding replanning and review pauses. Recorded demos restore their exact saved rate, including earlier 10×-boost demos. Every drone and the absolute weather clock consume the same step. Frame intervals are not capped at 100 ms; slow callbacks retain their elapsed time. Speed-slider changes divide an interval at the time of input. Both starting and resuming use the same movement routine, which consumes all launch delays, airport holds, and refueling events crossed within a frame. Steps stop at published weather transitions while impact assessment runs, so the transition is checked at the same mission time across playback speeds. Manual pauses and replay presentation holds do not consume flight time.

`node tests/e2e/test_animation_timing.js` exercises both animation callbacks with 60 fps, 250 ms, 1-second, and long frame gaps; it checks speed changes, multiple refueling stops, delayed departures, completion, pause/resume, and weather boundaries. These controlled callback tests do not measure real Chrome rendering performance. The diagnostic page reports time actually lost against sampled frame intervals and computes distance agreement using consumed hold time.

The weather slider provides at least 24 hours of browsing range and extends at least 12 hours beyond a longer flight's estimated arrival. All published advisories in the retrieved area can be inspected, while solving uses only the selected mission Start–End window. Extra slider range does not imply future NOAA coverage. If the solved ETA exceeds End, Use flight ETA updates the planning window and retrieves weather; a new solve is required before accepting the draft.

## Single-world map coordinates

Grid and Map use one geographic world. Street and satellite tiles do not repeat;
panning stays within the world and editing ignores blank space outside it. Map
projection, selection and editing share the same coordinates, including when the
canvas is resized. Date-line legs split at the flat map's edges.

Older files and saved browser views can contain longitudes from repeated map
copies (for example, DFW at −457°). Import repairs these values once, in memory;
the original file is unchanged. New solver requests reject mission coordinates
outside latitude ±90° / longitude ±180°. Provider weather polygons retain their
separate date-line handling. After updating, hard refresh and re-import the mission.

Regression coverage: `node tests/e2e/test_map_coordinates.js`,
`node tests/e2e/test_route_visibility.js`, and
`venv/bin/python -m pytest tests/test_mission_coordinates.py`. These cover actual
edit/import/export handlers, legacy recovery, route/airport/drone/checkpoint
alignment, date-line paths, and solver input validation. The original DFW–DXB
file was also repaired and solved through the real backend: 12,952.66 km, with
the returned endpoints matching the airports exactly.

Fit mission uses the same current environment as the canvas, including staged
live edits. View synchronization retains Leaflet's tile, world-bounds and weather
area listeners. Previously, unqualified `off('move')`, `off('moveend')`, and
`off('zoom')` calls removed those listeners and left the raster tiles stale while
the mission overlay updated. A UK–Egypt reproduction fitted the map to zoom 4.9
while its tiles remained at zoom 3, enlarged 3.73 times. With listeners preserved,
Leaflet selects zoom-5 tiles. Map switching and Fit now complete synchronously;
Fit does not animate or alter the mission coordinates.

The real Leaflet 1.9.4 regression in `tests/e2e/test_leaflet_fit_mission.js`
checks actual tile transforms against canvas landmark positions, repeated fits,
fractional zoom, pan, Grid/Map switching and staged edits. It uses jsdom without
tile network requests. Install test dependencies outside the repo with
`npm install --prefix /tmp/isr-fit-mission-tests leaflet@1.9.4 jsdom@26`, then run
`NODE_PATH=/tmp/isr-fit-mission-tests/node_modules node tests/e2e/test_leaflet_fit_mission.js`.

## Flight marker and route geometry (October 12)

Map and 3D now use `interpolateFlightPosition` for the same great-circle curve
as the backend animation frame. Previously Map joined projected vertices with
straight lines, playback interpolated latitude/longitude, and 3D interpolated a
geodesic. Those curves met at each vertex but diverged between them, producing
the repeated apparent departure and return. Follow merely made it easier to see.

Map samples the shared curve to a 0.2 CSS-pixel tolerance at the current zoom,
reusing samples while the route and zoom stay unchanged. Coordinate edits
invalidate the cache even when an array is changed in place. 3D draws the same
curve with at most 1 km Cartesian chords (about 2 cm curvature error), rather
than having Cesium replace it with a different curve. Marker positions, legacy
playback densification, checkpoint cuts and inverse checkpoint lookup use the
same geometry; the authoritative distances and clock are not resampled.
Tests compare actual Map/Grid drawing and the 3D marker to independent
interior coordinates, including non-midpoint fractions, endpoint arrival,
stationary holds, date-line travel and frozen checkpoint history. The reproduced
Map error dropped from about 36 pixels to below 0.1 pixel in the tested leg.

Run `node tests/e2e/test_flight_position.js`,
`node tests/e2e/test_flight_projection.js`,
`node tests/e2e/test_route_visibility.js`, and
`node tests/e2e/test_route_visibility_3d.js`. These execute production geometry
and rendering code with canvas/Cesium test doubles; they are not a Chrome visual
inspection. The existing clock tests and recorded weather checkpoint integration
also pass with the shared interpolation.

## Presentation pathway validation

Live NOAA ingestion and recorded-demo import close polygon rings that omit the
repeated first coordinate. A triangle needs three distinct vertices and a fourth
closing entry in the normalized GeoJSON; the previous frontend check rejected
three-entry triangles and aborted the entire capture with a misleading "Demo
weather" error even in Live NOAA mode. Normalization preserves every vertex and
advisory, including polygon holes, without changing the source capture. Invalid
coordinates or fewer than three distinct vertices still fail validation.
Regression coverage is in `tests/test_weather_feed.py` and
`tests/e2e/test_weather_geometry.js`.

On October 12, 2026 at 15:38:29 UTC, actual NOAA retrieval returned 259 advisories. The live mission test included seven No fly polygons and the real solver found a route visiting all eight targets in 2.847 seconds. This is one measured solve, not a latency guarantee or a prediction of presentation-day weather. The private kit includes `Live-weather-test-result.json` and the recorded checkpoint integration report.

Offline tests exercise portable import/export, source switching, Reset baseline restoration, stale request rejection, request/Accept consistency, and animation timing. The recorded integration uses the real backend with the original capture. These checks do not verify browser rendering or the complete new UI in Chrome; agent browser access remains blocked. Kamal previously verified the earlier checkpoint and boosted playback flow through arrival.
