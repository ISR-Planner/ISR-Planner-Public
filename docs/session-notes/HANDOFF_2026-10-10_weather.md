# ISR Planner handoff — October 10, 2026

## Where we stopped

Kamal is preparing a DoW presentation for Wednesday. The replay fixes and Env-tab target-list fix were tested locally, accepted, committed, and pushed to main. Weather work has started on a separate local branch, `feat/noaa-weather`, based on main commit `0b43415`.

Kamal reviewed the interactive weather mockup and said, “OK. We can continue this later.” Resume with this design and the agreed semantics below. Weather routing and production UI have NOT been implemented.

## Resume at home

1. Read `CLAUDE.md` and this handoff before editing.
2. Fetch origin and inspect local changes before switching branches. Bring the home checkout's main up to date without overwriting local work. Latest pushed commit from this session: `0b43415`.
3. Create or switch to `feat/noaa-weather`. This branch was created locally and has NOT been pushed.
4. Transfer the companion `weather-prototype-2026-10-10.patch` and this handoff to the home machine. The patch adds `scripts/fetch_noaa_weather.py` and `webapp/weather_mockup.html`. Run `git apply --check /path/to/weather-prototype-2026-10-10.patch`, then `git apply /path/to/weather-prototype-2026-10-10.patch`. Do not apply it again if these files already exist.
5. Start the local server with `./run_planner.sh` at http://localhost:8893. View the mockup at http://localhost:8893/static/weather_mockup.html.

The weather files are untracked, so a normal git pull at home will NOT retrieve them. No weather commit, push, or deployment was authorized or performed. The patch contains the prototype files only, not this handoff or unrelated untracked work.

## Agreed weather behavior

Each weather cell has a horizontal polygon, lower and upper altitude bounds, a validity period, and one of two modes:

- **No fly:** acts as an NFZ when a flight path intersects the active weather volume. Applies to transit, climb, descent, takeoff, and landing. An airport inside the volume does not override the restriction.
- **Can fly:** transit is allowed. Effects apply ONLY to target sensing within the polygon and applicable altitude band: detection-radius and loiter-time adjustments. Airports are excluded from these modifiers and otherwise retain existing behavior.

Proposed effect representation: multipliers applied to the drone's baseline settings for the target type. The mockup illustrates detection radius ×0.5 and loiter time ×1.5. These are illustrative planner settings, NOT NOAA measurements or validated operational thresholds. Preserve baseline settings rather than repeatedly modifying them.

Proposed overlap rule, not separately confirmed: smallest detection multiplier, largest loiter multiplier; No fly takes precedence. Exact hazard-to-mode mapping and modifier defaults remain to be decided.

Save the weather snapshot with each mission segment so replay is reproducible. Explicitly fetch/preview/apply updates rather than silently changing a solved mission. Treat missing altitude data explicitly; do not invent altitude bounds without a visible planning assumption.

## NOAA investigation and prototype

Selected source: NOAA Aviation Weather Center.

- Documentation: https://aviationweather.gov/data/api/
- Schema: https://aviationweather.gov/data/schema/openapi.yaml
- Current combined SIGMET endpoint: https://aviationweather.gov/api/data/sigmet?format=geojson
- G-AIRMET endpoint: https://aviationweather.gov/api/data/gairmet?format=geojson
- NOAA map: https://aviationweather.gov/gfa/#sigmet

The downloaded schema marks separate `airsigmet` and `isigmet` endpoints deprecated. Use `sigmet`, which includes domestic and international SIGMETs. G-AIRMETs cover the contiguous United States. The API does not allow browser CORS, so eventual integration should fetch through our backend. NOAA requests a custom User-Agent and limits frequency; respect published limits and cache responses.

`scripts/fetch_noaa_weather.py` is a standalone standard-library snapshot fetcher, NOT a planner API or routing adapter. It fetches both products by default, preserves source GeoJSON verbatim, records source URLs and retrieval timestamps, and prints hazard/geometry counts. It uses a 30-second request timeout, validates FeatureCollection structure, handles HTTP 204 as an empty successful feed, and fails rather than converting upstream errors into clear weather. Output uses exclusive file creation to avoid overwriting a previous snapshot.

Run with a new output filename:

```bash
python3 scripts/fetch_noaa_weather.py --output /tmp/noaa-weather-home.json
```

Live test on October 10 succeeded: 155 SIGMET features and 34 G-AIRMET features. Counts change with live conditions. SIGMET geometries included Polygon and MultiPolygon. G-AIRMET geometries included polygons and freezing-level LineStrings; do not turn those lines automatically into NFZs. Of the SIGMET features in that snapshot, 114 lacked at least one explicit altitude bound.

Original snapshot on this machine: `/tmp/isr-noaa-weather-20261010.json`. This temporary file is not in Git or the patch. Fetch a new snapshot at home, or copy this file separately if the exact sample is needed. Source altitude references, units, surface limits, and validity semantics still need normalization before routing integration.

## Mockup

File: `webapp/weather_mockup.html`.

This is an isolated HTML/SVG design preview, not part of the planner's main interface. No new dependencies. Both the polygon and corresponding button select a weather details panel.

- W1: red outline, translucent red fill, diagonal hatching, “NO FLY.” Sample blue route detours around it.
- W2: cyan outline/translucent fill, “CAN FLY.” Dashed target rings show baseline detection radius; solid smaller rings show effective radius. Affected targets: T5, T6, T7.
- SAM zone remains yellow to distinguish it from weather.
- Details show altitude, time, source, mode, and effects. W1 includes an expandable NOAA advisory excerpt.
- Grid/Map/3D labels are visual placeholders; only the 2D Grid preview exists. No actual 3D weather volumes or solver behavior yet.
- Geometry/routes are illustrative. W1 uses the supplied advisory text but repositions its polygon for demonstration. W2 is clearly labeled a manual cloud scenario. Neither is a live georeferenced weather overlay.

Headless Chromium rendered the mockup and verified both detail panels are selectable. Screenshots on this machine: `/tmp/isr-weather-mockup.png` and `/tmp/isr-weather-mockup-nofly.png`; these are not included in the patch. Python compilation and diff whitespace checks passed for the prototype.

Suggested next step: agree on how users assign Can fly / No fly and target modifiers, then define a normalized weather-cell schema and backend NOAA adapter. Handle altitude references, missing bounds, MultiPolygon geometry, advisory validity, and non-area products before connecting weather to solver constraints. Keep weather development isolated from the demonstrated production build.

## NOAA example discussed with Kamal

PAPA 1, October 10: `OCNL SEV TURB BLW 070. RPTD BY ACFT. CONDS ENDG 2139Z.`

Meaning: occasional severe turbulence below 7,000 ft MSL, reported by aircraft, valid 17:39–21:39 UTC (12:39–4:39 PM Chicago daylight time). The polygon is defined by distances/directions from navigation references; state abbreviations do not mean the entire states are affected. A surface lower bound would be a conservative planner assumption because the advisory gives only an upper bound. Expiry/expected ending time does not guarantee clear weather. Treating an advisory as No fly is our planning rule, not a claim of a legal flight restriction.

## Fixes completed and pushed in this session

### `6c3b256` — fix(replay): restore created segment environments

Previously, a newly created mission replay restored segment-0 routes but retained the final environment, exposing future targets. Imported replay already restored its environment.

Added `getCreatedReplayEnv` in `webapp/isr.js` to restore the current segment's environment and cumulative target history. Live-created replay now restores segment-0 environment/configs and reveals saved targets at cuts, including unassigned targets.

Reproduction: `~/Downloads/isr_seg_20261010-1141_N4_1.json`. Target counts: 4 initially, 6 at C1, 8 at C2, 10 at C3. `tests/e2e/test_segment_replay_targets.js` covers reconstructed completed-creation state and imported replay in Grid, Map, and real Cesium 3D, including two repeated replays. Test setup waits for asynchronous initial environment loading to prevent test data being overwritten. Loiter and mission-sequence checks also passed.

Kamal confirmed clean local segment creation and replay, requested commit/push, and subsequently reported Railway working.

### `0b43415` — fix(env): retain target history across cuts

Env tab listed D2's last-segment allocation (T1, T2, T5) while its target count included T3 and T6 from earlier segments. Allocation badge incorrectly showed 7/9.

Env lists now combine visited ownership with current planned targets, deduplicate, exclude tankers and targets owned by another drone, and retain visits for disabled/lost drones while excluding their remaining plans. Route and animation stats refresh the allocation display.

Reproduction: `~/Downloads/isr_seg_20261010-1259_N3_5.json`. D2 retains T3, T6, T1, T2, T5; badge 9/9. Browser regression `tests/e2e/test_segment_target_lists.js` passes reconstructed creation-state and imported repeated replay. `tests/e2e/test_env_tab.js` passed eight checks; mission-sequence checks passed twelve.

Kamal confirmed the local fix and explicitly requested commit/push. Push succeeded. Deployment completion and a post-deployment Railway retest of this particular commit were not independently verified in this session.

Earlier deployed replay fixes from the previous session: `beda885` and `91d5e03` (loiter/cut alignment, imported environment rewind, 3D scene refresh).

## Agent credential issue — unresolved confirmation

On Railway, “Solve this Mission” returned “Provider authentication failed.” The public `/api/llm/providers` endpoint reported OpenAI configured; a POST to `/api/llm/test` with provider `openai`, model `gpt-4o` returned `success: false`, “Invalid API key.” No credentials were exposed.

Kamal received instructions to create an OpenAI API key, replace the Railway production service's `OPENAI_API_KEY`, and deploy the staged variable change. He has not confirmed completing those steps. Verify the connection before further Agent debugging; do not assume this is fixed. Never ask him to paste a key into chat.

## Working rules and deferred issues

- Explain investigation and correction before editing. Fix demonstrated causes, not speculative workarounds.
- Test functional changes on localhost with appropriate unit and browser checks.
- Commit/push only when explicitly requested. Main pushes deploy Railway. Previous commit authorizations do not cover new weather work.
- Mockups/design precede UI changes. The isolated weather mockup has now been shown to Kamal.
- For the presentation, start with a fresh environment. Editing an environment after solving can still produce trouble; Kamal accepted deferring that issue.
- Kamal reported a local segment-0 trajectory offset miles east of airports, correcting at C1, and explicitly deferred it. Later local success reports did not isolate or diagnose that issue.
- 3D editing of targets, airports, SAMs, and tankers remains a separate deferred feature.
- A broader unified fresh-flight initialization refactor remains unapproved.
- Preserve unrelated untracked directories: `docs/session-notes/restore_2026-09-23_before_unified_ops/` and `experiments/`.

## Local browser-test setup

Playwright was installed outside the repository. This machine uses:

```bash
PLAYWRIGHT_BROWSERS_PATH=/private/tmp/isr-browser-check/browsers \
NODE_PATH=/private/tmp/isr-browser-check/node_modules \
node tests/e2e/test_segment_target_lists.js \
  http://localhost:8893 ~/Downloads/isr_seg_20261010-1259_N3_5.json
```

At home, use an available Playwright installation or install it separately. Temporary paths and Downloads reproduction files may not exist there. The JSON missions remain external reproduction inputs, not repository fixtures.
