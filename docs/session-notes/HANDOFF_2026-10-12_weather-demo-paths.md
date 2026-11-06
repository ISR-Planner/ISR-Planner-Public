# Weather demo pathways — October 12, 2026

This updates the earlier [weather playback handoff](HANDOFF_2026-10-12_weather-playback.md). The presentation now has two pathways in `/static/weather_preview.html`: recorded scenarios and current NOAA. The normal `/` planner remains separate. This document records implementation and tests; the current runtime and verified deployment are identified below.

## Current test environment — October 13

Kamal chose **Railway for Agent testing for now**. Runtime commit `26b394f` (“Honor Agent provider selection and local project credentials”) is pushed to `main` and `feat/noaa-weather`, and Railway reported a successful deployment for that exact commit. A post-deployment check confirmed the new script version and a successful Agent reply with an explicit OpenAI/gpt-4o selection (1.6 seconds). Open [the Railway weather preview](https://isr-planner-production.up.railway.app/static/weather_preview.html) in Chrome; an address starting with `localhost:8893` uses the separate local server.

For an Agent test: **hard refresh → Env → Import** the mission JSON → **AI Settings → OpenAI → gpt-4o → Test Connection → Save Settings** → **Agents**, enter the mission request and click **Send**. For recorded weather, use Weather → Load demo instead of the plain mission import and keep its restored dates.

The local Agent credential repair is **deferred**, not completed: its inherited OpenAI key and its different project `.env` key were both rejected. No key was replaced, and the local server has not been restarted with a repaired credential. Resume that task only when requested, after a valid key is entered privately. The ordinary Run Planner solver does not require an LLM key.

## Office transfer

**Before importing:** It is advisable to hard refresh before every fresh mission/environment import or **Load demo**, on both localhost and Railway. In Chrome, use **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux). This reloads the latest scripts and starts a fresh page; then import the file to initialize the mission and help prevent stale state from a previous run carrying over. Include this step in office handoff prompts and demo instructions.

Bring the **ISR Weather Demo Kit** ZIP from Downloads separately from the repository. Its mission and bundled weather files are private local artifacts, not committed or copied into the public website.

The kit directory is `/Users/kamalali/Downloads/ISR Weather Demo Kit/`. Its two scenario files are:

- `01_Recorded_West_Northeast_Gulf.weather-demo.json`: the mission plus original NOAA capture, original dates, map bounds, and playback speed.
- `02_Live_West_Northeast_Gulf.environment.json`: the same original environment, ready to retrieve current weather.

The kit also contains recorded-checkpoint and live-weather test reports. Update the office checkout after checking for local changes; preserve office work and do not use a hard reset. Run `./run_planner.sh`, then open `http://localhost:8893/static/weather_preview.html` in Chrome. For Railway, verify that the intended revision has deployed before using its corresponding preview URL.

## Recorded presentation

1. **Hard refresh first**, then choose **Load demo** and select the recorded weather-demo file.
2. Keep its restored dates: **October 11, 2026, 20:24 CDT → October 12, 12:24 CDT**. UTC is **October 12, 01:24 → 17:24**.
3. Click **Run Planner → Accept → Animate**. The file sets 1,280× playback through the speed slider and 10× boost. For faster playback, change the Boost selector to **100×**, giving **12,800×** at the maximum slider setting. A fresh page defaults the selector to 100×; loading an older demo restores its saved 10× rate.
4. When weather creates a diamond and a revised draft, click **Accept → Animate** to continue.
5. To repeat, click **Reset**, confirm discarding cuts if prompted, then **Animate**. Reset restores the original route, clock, and weather baseline. For a fresh solve workflow, hard refresh before loading the demo again.

The original route visits T8, T2, T3, T5, T7, and T6. At **02:55 UTC / 21:55 CDT**, the relevant advisories expire. The verified regression creates one C1-1 checkpoint, preserves visited T8 and fuel, and produces a revised route including T1 and T4. It remains paused for review. Expiry prompts reassessment; it is not proof of clear weather.

The recorded source stays recorded through refreshes and checkpoint replans. It contains the advisories and forecasts available at capture time, not a sequence of later NOAA observations. **Save demo** exports this original scenario for re-solving; it does not export an accepted movie or route replay.

## Live presentation

1. **Hard refresh first**, then import the plain environment file. This clears the previous recorded scenario and selects **Live NOAA** with current mission times.
2. Click **Fit mission → Get forecast**. Adjust the mission window if needed, then **Update forecast**.
3. Click **Run Planner → Accept → Animate**.
4. Review checkpoint replans with **Accept → Animate**.

Switching the source clears the old accepted route. To plan again after accepting, hard refresh, import the original environment again, retrieve weather, then solve and accept. Animate blocks weather that differs from the accepted route; Reset restores that route's original weather. Selecting past dates filters the latest package; it does not request archived weather. The long slider reserves planning space and does not extend actual forecast coverage. No fly avoidance uses the fixed 5 km margin; other advisories remain display-only.

## Changes and verification

Reset and import now clear stale mission clock, completion flags, checkpoints, and accepted history. Reset restores the first captured weather and dates. Old asynchronous solve or weather responses cannot modify a newly imported or reset mission.

Actual live NOAA retrieval at **2026-10-12 15:38:29 UTC** returned **259 advisories**. The mission test applied **seven No fly polygons** and the real solver visited **all eight targets in 2.847 seconds**. Conditions and solve duration will vary on Wednesday.

Offline tests cover recorded load/save, source changes, invalid-file protection, Reset, repeated imports, stale responses, weather acceptance, and timing. The recorded integration uses the real backend and checks the same checkpoint across playback speeds. For the original October 12 validation, browser access was unavailable: those are source/controller/backend tests, not verification of the weather flight UI in Chrome. The October 13 Railway Agent checks above do not replace a full rendered weather-demo test. Kamal previously confirmed the earlier boosted mission flow through final arrival.

Follow-up: importing cleared route Show flags, so fresh solves were invisible in Map while 3D ignored those flags. Fresh solves now show new drawable routes by default, preserving explicit Hide choices. Both views honor the same route visibility controls. The DFW–DXB regression reproduced the missing Map stroke before the fix and now verifies actual canvas drawing, waypoint fallback, checkpoint history, and Show controls. A separate Cesium regression verifies visibility without losing camera geometry. Refresh the page to load the updated scripts; in an existing session, **Env → Drones → D1 → Show** reveals the solved route without re-solving.

The route visibility follow-up passes both new regressions, weather acceptance/demo/lifecycle, mission sequence, and chase-camera tests. `make check` stops on existing isort formatting in unchanged `server/solver/sam_distance_matrix.py` and `server/api/llm_routes.py`; it is not a clean full quality-check result.

Useful checks:

```bash
node tests/e2e/test_weather_demo_paths.js
node tests/e2e/test_weather_lifecycle.js
node tests/e2e/test_weather_accept.js
node tests/e2e/test_animation_timing.js
node tests/e2e/test_route_visibility.js
node tests/e2e/test_route_visibility_3d.js
```

## Routing performance follow-up — October 13

The local solve reported at 00:27 contained five RQ-4Bs, 17 targets, and 28 SAMs, with weather off. At cruise altitude the SAMs form 24 separate polygons with 1,734 vertices. The original first visibility-graph build checks 1.5 million vertex pairs in Python; a bounded profile spent 97% of its time there before allocation. The existing graph cache only helped after that first build. Subsequent profiling also found redundant Dijkstra queue entries and repeated visibility checks for the same mission endpoints.

The shared router now batches the original geometry predicates with NumPy, reuses prepared polygon data and bounded endpoint connections, and shares concurrent builds of identical graphs. Dijkstra retains only better labels, preserves equal-distance path ordering, and reuses rounded coordinate keys. Obstacle resolution, boundary tolerances, altitude handling, fuel/loiter rules, and route constraints are unchanged.

The full reconstructed mission completed in **65.20 seconds from a fresh cache**, including **21.14 seconds** to construct the graph, post-optimization, and detection-radius finalization. It returned all 17 targets and five routes. Route sequences and distances exactly matched earlier completed test runs; independent segment/polygon checks found no interior crossings. Private mission coordinates and diagnostic cache files remain outside the repository.

Independent differential checks covered 50,541 geometry cases and 1,610 shortest-path cases. Durable regressions are `tests/path_planning/test_visibility_acceleration.py` and `tests/path_planning/test_dijkstra_acceleration.py`, including cache bounds, concurrency, failure recovery, exact coordinate distinctions, and query isolation. The stalled local worker was restarted under the existing port 8893 supervisor. An open mission can be solved again without re-importing it.

## ToT and loiter follow-up — October 13

The timing audit confirmed that ToT already included loiter at earlier targets, but used the original shared distance matrix while animation used the final flown spans. Detection-radius shortcuts and per-drone altitude matrices could therefore produce different arrival times. A controlled example reported a synchronized arrival at 30 minutes while playback reached the target at 20 minutes.

ToT and animation now share `server/solver/route_timeline.py`. The scheduler derives travel from the final frame's waypoint arrival anchors and adds preceding loiter once. Refuel fuel legs have their loiter removed before constructing those holds. The selected target's own loiter begins **after** its synchronized arrival; ToT does not synchronize loiter completion. The configured arrival tolerance still applies.

Launch delays and airborne wait distances retain full precision; only displayed timing metadata is rounded. Re-synchronization removes prior waits before computing replacements, so repeated calls and changed selections do not accumulate wait fuel. Agent responses use any updated speed/loiter configuration for both scheduling and playback. The manual `/api/solve` response now preserves final spans and supplies the environment/configuration when building animation frames, matching the main solve path.

Scheduled frames explicitly disable the player's extra one-second airport presentation pauses. Refuel accounting remains active. Ordinary unscheduled routes and older frames retain their previous pause behavior.

Regression coverage is in `tests/test_time_sync_timeline.py` and `tests/e2e/test_animation_timing.js`: earlier/selected-target loiter, detection shortcuts, altitude matrices, intermediate refuels, nonunit speeds, zero/nonzero tolerance, both waiting strategies, repeated sync, changed selections, agent/manual response paths, and 1×/12,800× playback. Related fuel, flight-profile, altitude, segment-loiter, weather-clock, checkpoint-controller, and real-solver detour-checkpoint tests also pass. Independent differential checks covered 120 varied timing cases, each synchronized twice. These are automated backend/player tests, not a new visual check in Chrome.

To test an existing environment, hard refresh with **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux), import the environment or load its demo, then **Run Planner → Accept → Animate**. Previously accepted or exported routes retain their old timing until solved again; restarting their animation alone does not recalculate ToT.

### ToT indicators in 3D

The 3D renderer now displays the same solved ToT selections as Grid/Map: cyan breathing double rings and `SYNC Dn` labels, or static amber dashed rings for blocked synchronization. The missing rings were a rendering omission; the solver output was already present. Rings appear on the draft before Accept, use the target's position and elevation, pulse while flight is paused, and are removed when the synchronization summary is cleared or the view closes. The new `tests/e2e/test_time_sync_markers_3d.js` covers status, pulse, elevation, snapshot fallback, and view lifecycle; existing route/flight-curve and chase-camera regressions also pass. These are automated renderer tests, not a visual Chrome check.

To verify the four-target example, **hard refresh → Import `ToT.json` → Run Planner → switch to 3D**. The private JSON remains in Downloads and is not committed. The new 3D script version is loaded by both the normal planner and weather preview.


## Agent provider follow-up — October 13

Agent Send previously read AI Settings but omitted them from `/api/agents/reason`, which always used the server's `LLM_PROVIDER` / `LLM_MODEL` defaults. Test Connection used the selected settings, so it could test a different provider from the Agent. Agent Send now carries only provider/model and the existing Ollama host option. Hosted credentials remain on the server; client keys and hosted endpoint overrides are ignored. Calls without settings retain the server defaults. Debug now identifies the selected model instead of the inaccurate hardcoded “Opus 4.6” label.

Railway's existing OpenAI/gpt-4o credential passed Test Connection on October 13. The actual Agent endpoint also replied successfully and solved a synthetic A1–T1–A2 mission in 5.0 seconds, with 5 points and 10.198 distance. These successful checks preceded this provider-selection fix; no Railway credential was replaced. Kamal then confirmed the failing page was `http://localhost:8893/static/weather_preview.html?start=empty`. The local server inherited an OpenAI key different from the project `.env`, and the existing `load_dotenv()` preserved that inherited value. Both the inherited key and the separate `.env` key were rejected in direct connection tests. Localhost therefore needs a valid project credential; a browser refresh alone cannot fix it.

On the deployed Railway page, **hard refresh before importing**, then use AI Settings → OpenAI → gpt-4o → Test Connection → Save Settings. Send the mission request through Agents. Regression coverage exercises the frontend request and real backend request-to-factory path, including provider switching, server defaults, checkpoint payloads and exclusion of browser credentials.

The local `run_planner.sh` launcher now loads the explicit project `.env` with override enabled before execing uvicorn, so stale parent-shell credentials cannot shadow that file and the reload worker inherits the same values. Railway starts uvicorn directly and retains its own environment-variable precedence. After updating the local key privately, restart with `./run_planner.sh`. No keys are included in code, logs, tests or this handoff.

Validation: 96 relevant Python tests, two isolated local-launcher tests, and the frontend Agent regression passed. Format and lint checks passed. The full `make check` type-check remains blocked by four pre-existing Optional-type errors in `path_planning_core/boundary_navigation.py`; they are outside this Agent change.
