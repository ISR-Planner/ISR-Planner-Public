# ISR Planner weather playback handoff for the office

Recorded October 12, 2026, after the overnight weather debugging session. Kamal confirmed that the mission now solves, pauses for a weather checkpoint, produces the revised route, and animates through to the end after Accept and Animate. The 10× playback boost makes testing practical. The next task is to repair Reset and repeated imports so another run does not require a page refresh.

## Resume at the office

**Before importing:** It is advisable to hard refresh before every fresh mission/environment import or **Load demo**, on both localhost and Railway. In Chrome, use **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux). This reloads the latest scripts and starts a fresh page; then import the file to initialize the mission and help prevent stale state from a previous run carrying over. For the current recorded/live workflow, read the [updated weather demo handoff](HANDOFF_2026-10-12_weather-demo-paths.md).

Repository: `ISR-Planner/isr-planner`. Work branch: `feat/noaa-weather`. The release is also pushed to `main`, the branch used by Railway. The previous deployed baseline was `f5bfc68`; this handoff accompanies the weather checkpoint and playback stabilization commit.

In your office checkout, inspect local changes before switching branches or pulling. Preserve any office work; do not use a hard reset to update.

```bash
git status --short
git fetch origin
git switch main
git pull --ff-only origin main
git log -1 --oneline -- docs/session-notes/HANDOFF_2026-10-12_weather-playback.md
./run_planner.sh
```

The startup script uses the existing `.venv` or `venv` and starts port 8893. This machine uses `venv`. Open these addresses in Chrome:

| Page | Purpose |
| --- | --- |
| http://localhost:8893/static/_weather_diagnostics/index.html | Repeat the recorded NOAA test used tonight |
| http://localhost:8893/static/weather_preview.html | Retrieve current NOAA advisories |
| http://localhost:8893/ | Normal planner; shares the animation clock and boost changes |

Railway preview: https://isr-planner-production.up.railway.app/static/weather_preview.html. A successful Git push does not itself establish deployment success; check the deployment for the pushed commit before treating Railway as updated.

## Bring the original mission file

The test environment is `isr_env_20261011-2046_N1_1.json`, currently in `/Users/kamalali/Downloads/`. It is a plain `isr_env_v1` environment with airports A1–A3, targets T1–T8, and only D1 enabled as an RQ-4B from A1 to A3. D1 has a 22,780 km fuel budget and 574 km/h nominal speed.

The private mission JSON is not committed or copied into the served website. Bring it separately to the office. The repository includes the public NOAA capture and diagnostic scripts, so those will arrive with the pull.

## Reliable workflow for another run

Until Reset is repaired, begin each fresh run with a page refresh, including when using the same environment again.

1. Hard refresh first: **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux), then import the original environment JSON.
2. Click Fit mission, then Get forecast.
3. Check the mission start, end, and time zone. After changing the times, click Update forecast.
4. Click Run Planner, wait for the draft, then Accept and Animate.
5. Enable 10× boost and move the speed slider right for 1,280×. The displayed multiplier is the actual rate.
6. At a weather checkpoint, wait for the revised draft, then Accept and Animate again.

On the diagnostic page, the first Get forecast after import initializes the recorded time window. Make any Central Time or duration changes after that initialization, then Update forecast before solving. Keep Weather checkpoints and Follow mission checked. The Run comparisons button is for timing benchmarks; normal manual Run Planner exercises actual weather replanning.

A 16-hour flight takes about 45 seconds of playback at 1,280×, excluding weather assessment, solver time, and review pauses. New weather requires a new solve; Get forecast alone does not rebuild an accepted route.

## Recorded weather and expected checkpoint

The diagnostic page uses `webapp/_weather_diagnostics/noaa_20261012T012337Z.json`, retrieved October 11 at 8:23:37 PM CDT, or October 12 at 01:23:37 UTC. Its default mission starts October 11 at 8:24 PM CDT, or October 12 at 01:24 UTC, and ends 16 hours later.

The initial route visits T8, T2, T3, T5, T7, and T6, omitting T1 and T4. The advisories associated with the omitted targets expire at October 11, 9:55 PM CDT / October 12, 02:55 UTC. In the verified test, the controller pauses once, creates C1-1, preserves visited T8 and remaining fuel, and requests a fresh route containing T1 and T4. The revised draft stays paused for explicit review. Staying at the diamond before Accept and Animate is expected.

The current NOAA page retrieves current advisories; moving Start into the past does not retrieve historical weather. A longer mission window or slider does not extend NOAA coverage. Expired advisories trigger reassessment of missed targets, with a review warning; expiry is not treated as confirmed clear weather. No fly polygons use the existing fixed 5 km avoidance margin. Other advisories are displayed without changing detection radius or loiter.

## Changes completed

- Current NOAA retrieval and weather impact API routes are registered. Retrieval failures are visible and block a weather solve until refreshed.
- Weather changes affecting the mission create one checkpoint and request a fresh draft. The diagnostic page no longer reuses its initial cached solve for manual replans.
- Both animation loops use one mission clock and movement routine. Slow callbacks retain elapsed time, airport/refuel events consume the full time step, and changing speed applies the old and new rates to their respective intervals.
- Playback rates are literal. The optional 10× boost increases the maximum from 128× to 1,280× while keeping drone and weather time aligned.
- Weather transitions stop playback at the published mission time while assessment runs. Manual pauses and replay presentation holds do not advance the flight clock.
- The weather slider reserves at least 24 hours and extends beyond a longer estimated flight. Extra browsing range does not imply forecast availability.
- Accept checks the weather snapshot used by the draft and the selected mission times. Panning or zooming to inspect that draft no longer blocks acceptance; changing its weather or times still requires a new solve.
- Solve and Accept blockers are shown beside Mission Control as well as in Weather and Debug.

## First task next session

Fix the lifecycle of Reset and import, with a regression that runs the same mission twice in one page session.

`resetMission()` in `webapp/isr.js` restores segment-zero routes and sets READY_TO_ANIMATE. It clears the aircraft elapsed time but does not reset the weather preview closure or controller. Weather checkpoint replanning changes `planningStart` and the displayed Start to the checkpoint time, leaving them inconsistent with the restored original route after Reset. Refreshing weather can therefore combine an old accepted route with a checkpoint-time weather selection.

The preview's `loadFromJson` wrapper clears its snapshot, clock origin, planning start, and weather checkpoint controller. However, the shared import path does not consistently clear the previous `animation.elapsedMissionSeconds`, `missionComplete`, `state.checkpoint`, `missionState.committedSegments`, or `seg0FullSolution`. Re-import alone is not a reliable clean restart. End mission changes the backend mission status; it is not a full local reset.

Define the intended behavior of replay versus a fresh solve, then reset the route, clocks, fuel/visits, checkpoint state, weather baseline, and UI together. Preserve the working first-run flow. Verify two complete runs, a reset after a weather cut, and an import of the same or different plain environment without reloading. Check that pending asynchronous weather work cannot write into the new run.

## Verification recorded

| Check | Result |
| --- | --- |
| Full Python suite | 488 passed, 3 skipped |
| Required format, lint, and type checks | `make check` passed; mypy checked 196 source files |
| Actual animation callbacks under controlled timestamps | 84 passed, including 1,280×, speed changes, refuels, final arrival, and exact weather stops |
| Weather acceptance pipeline | 11 passed, including map inspection, changed snapshots, exact checkpoint seconds, and Central Time |
| Weather clock, slider, solve gate, checkpoint controller, diagnostic replay, and segment loiter checks | Passed |
| Offline recorded mission integration | Seven speed patterns passed, including 1,280× and changing rates; exactly one cut and fresh solve, visits/fuel preserved |
| User browser test | Kamal confirmed successful solving and animation through the end with the boost |

The automated JS tests execute the shipped callbacks with controlled timestamps and DOM/render stubs. They do not measure real Chrome FPS. Agent browser access was blocked by a tool permission mismatch despite Kamal's authorization and localhost Always Allow setting; do not present offline tests as agent browser verification or bypass the tool restriction.

Useful commands from the repository root:

```bash
venv/bin/python -m pytest -q
PATH="$PWD/venv/bin:$PATH" make check
node tests/e2e/test_animation_timing.js
node tests/e2e/test_weather_accept.js
node tests/e2e/test_weather_checkpoint_controller.js
node tests/e2e/test_weather_diagnostic_replay.js
node work/diagnostics/weather_checkpoint_integration.js /path/to/isr_env_20261011-2046_N1_1.json
```

The offline integration script reads the private environment in memory and calls the Python backend directly. It requires the original mission file and a `venv/bin/python` environment.

## Source map

| File | Responsibility |
| --- | --- |
| `webapp/isr.js` | Animation clock, movement, boost, mission state, Reset, import, Accept |
| `webapp/weather_preview.js` | Forecast capture, mission time, slider, solve/accept guards, checkpoint integration |
| `webapp/weather_checkpoints.js` | Weather transitions, impact checks, one cut, fresh replan, paused review |
| `server/api/weather_routes.py` | NOAA retrieval/cache and impact endpoint |
| `server/solver/weather_impact.py` | Remaining-route impact, released targets, coverage uncertainty |
| `webapp/_weather_diagnostics/runner.js` | Recorded capture, controlled comparison runs, fresh manual solves |
| `tests/e2e/test_animation_timing.js` | Shared-clock, movement, speed, and boost regressions |
| `work/diagnostics/weather_checkpoint_integration.js` | Actual backend and checkpoint integration with the private mission |

See [Weather preview behavior](../WEATHER_PREVIEW.md) for the detailed UI and weather policy. Older October 10 handoff drafts and exploratory `work/visualizations/` files remain local and are outside this release.

To resume in a new Codex conversation: “Read docs/session-notes/HANDOFF_2026-10-12_weather-playback.md. The full first-run weather mission now works. Fix Reset and repeat import so I can run the same environment again without refreshing, while preserving the working clock, checkpoint, and Accept flow.”
