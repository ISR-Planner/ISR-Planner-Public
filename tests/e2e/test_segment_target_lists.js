// NODE_PATH=<playwright modules> node this-file <url> <seg.json>
const fs = require("fs");
const assert = require("assert");
const { chromium } = require("playwright");

async function check(url, filename) {
  const data = JSON.parse(fs.readFileSync(filename, "utf8"));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const workflow of ["created", "imported"]) {
    const page = await browser.newPage();
    page.on("dialog", d => d.dismiss());
    await page.goto(url);
    await page.waitForFunction(() => typeof loadSegmentedMissionFromJson === "function" && state.env !== null);
    const result = await page.evaluate(({ data, workflow }) => {
      loadSegmentedMissionFromJson(data, "target-lists.json");
      if (workflow === "created") {
        data.segments.forEach((saved, i) => {
          const segment = missionReplay.getSegment(i);
          segment.env = JSON.parse(JSON.stringify(saved.env));
          segment.drone_configs = JSON.parse(JSON.stringify(saved.drone_configs));
        });
        state.env = segmentedImport.getFullEnv();
        missionState.seg0FullSolution = segmentedImport.getSolutionForSegment(0);
        missionReplay.setCurrentSegmentIndex(data.segments.length - 1);
        state.animation.missionComplete = true;
        segmentedImport.clear();
      }
      document.getElementById("mc-replay-segments").checked = true;
      let callback;
      window.requestAnimationFrame = fn => { callback = fn; return 1; };
      window.cancelAnimationFrame = () => {};
      const runs = [];
      for (let run = 0; run < 2; run++) {
        startAnimation();
        state.animation.speedMultiplier = 8;
        for (let tick = 0; tick < 30000; tick++) {
          callback(tick * 100);
          state.animation.checkpointPause = null;
          if (state.animation.missionComplete) break;
        }
        runs.push({
          complete: state.animation.missionComplete,
          drones: [1, 2].map(did => ({
            did,
            listed: document.getElementById(`drone-targets-d${did}`).textContent.split(/,\s*/).filter(Boolean),
            visited: getVisitedTargetsForDrone(did, getRouteTargetLabels(state.routes[String(did)])),
          })),
          allocated: shownAllocation?.allocated,
          total: state.visitedTargets.length,
        });
      }
      return runs;
    }, { data, workflow });
    for (const run of result) {
      assert(run.complete);
      for (const drone of run.drones) {
        assert.deepStrictEqual([...drone.listed].sort(), [...drone.visited].sort(),
          `D${drone.did}: Env list must retain targets visited before cuts`);
      }
      assert.strictEqual(run.allocated, run.total, "allocation badge must include flown targets");
    }
    console.log(`PASS: ${workflow}: Env target lists and badge retain all visited targets across cuts and repeated replay`);
    await page.close();
    }
  } finally { await browser.close(); }
}
check(process.argv[2], process.argv[3]).catch(e => { console.error(e); process.exitCode = 1; });
