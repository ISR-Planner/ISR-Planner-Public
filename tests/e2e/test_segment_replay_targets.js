// NODE_PATH=<playwright modules> node this-file <url> <seg.json>
// Check rendered targets on created and imported mission replays in all views.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { chromium } = require("playwright");

async function check(url, filename) {
  const data = JSON.parse(fs.readFileSync(filename, "utf8"));
  const browser = await chromium.launch({ headless: true,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  try {
    const cases = ["created", "imported"].flatMap(workflow =>
      ["Grid", "Map", "3D"].map(view => ({ workflow, view })));
    for (const { workflow, view } of cases) {
      const page = await browser.newPage();
      const errors = [];
      page.on("pageerror", e => errors.push(e.message));
      page.on("dialog", d => d.dismiss());
      await page.goto(url);
      // Wait for the asynchronous startup environment load before seeding a
      // mission, otherwise its response can clear our routes mid-test.
      await page.waitForFunction(() => typeof loadSegmentedMissionFromJson === "function" && state.env !== null);
      await page.evaluate(data => loadSegmentedMissionFromJson(data, "replay-targets-regression.json"), data);
      if (workflow === "created") {
        await page.evaluate(data => {
          // Reconstruct the completed creation state using the saved solves.
          // Live Accept stores each segment's own environment; importing instead
          // stores the full environment and relies on its separate manager.
          data.segments.forEach((saved, i) => {
            const segment = missionReplay.getSegment(i);
            segment.env = JSON.parse(JSON.stringify(saved.env));
            segment.drone_configs = JSON.parse(JSON.stringify(saved.drone_configs));
          });
          state.env = segmentedImport.getFullEnv();
          missionState.seg0FullSolution = segmentedImport.getSolutionForSegment(0);
          missionReplay.setCurrentSegmentIndex(data.segments.length - 1);
          missionState.currentSegmentIndex = data.segments.length - 1;
          state.animation.missionComplete = true;
          segmentedImport.clear();
        }, data);
      }
      if (view === "Map") {
        await page.click("#btn-view-map");
        await page.waitForFunction(() => mapState.leafletMap !== null);
      } else if (view === "3D") {
        await page.click("#btn-view-3d");
        await page.waitForFunction(() => cesiumView.isActive(), null, { timeout: 60000 });
      }
      const runs = await page.evaluate(() => {
        let callback;
        window.requestAnimationFrame = fn => { callback = fn; return 1; };
        window.cancelAnimationFrame = () => {};
        document.getElementById("mc-replay-segments").checked = true;
        const snapshot = () => ({
          segment: missionReplay.getCurrentSegmentIndex(),
          importSegment: segmentedImport.getCurrentSegmentIndex(),
          drawnTargets: cesiumView.isActive()
            ? cesiumView._entityNames().filter(n => /^T\d+$/.test(n)).sort()
            : targetScreenPositions.map(p => p.t.id).sort(),
          envTargets: state.env.targets.map(t => t.id).sort(),
          originalTargets: state.initialEnvSnapshot.targets.map(t => t.id).sort(),
        });
        const runs = [];
        for (let replay = 0; replay < 3; replay++) {
          startAnimation();
          if (!callback) throw new Error(`Animation did not start: mode=${missionState.mode}, routes=${Object.keys(state.routes)}, permissions=${JSON.stringify(getUiPermissions())}`);
          state.animation.speedMultiplier = 8;
          // Draw the first actual animation frame before inspecting the canvas.
          callback(0);
          const scenes = [snapshot()];
          let lastSegment = 0;
          for (let tick = 1; tick < 30000; tick++) {
            callback(tick * 100);
            const currentSegment = missionReplay.getCurrentSegmentIndex();
            if (currentSegment !== lastSegment) {
              scenes.push(snapshot());
              lastSegment = currentSegment;
              state.animation.checkpointPause = null;
            }
            if (state.animation.missionComplete) break;
          }
          runs.push({ scenes, complete: state.animation.missionComplete });
        }
        return runs;
      });
      const allTargets = [...new Set(data.segments.flatMap(s => s.env.targets.map(t => t.id)))].sort();
      for (const run of runs) {
        assert(run.complete, `${view}: replay must finish`);
        assert.strictEqual(run.scenes.length, data.segments.length);
        for (const [index, scene] of run.scenes.entries()) {
          const expected = [...new Set(data.segments.slice(0, index + 1)
            .flatMap(s => s.env.targets.map(t => t.id)))].sort();
          assert.strictEqual(scene.segment, index);
          if (workflow === "imported") assert.strictEqual(scene.importSegment, index, `${view}: environment index must rewind with routes`);
          assert.deepStrictEqual(scene.envTargets, expected);
          assert.deepStrictEqual(scene.drawnTargets, expected,
            `${view}: future targets must stay hidden before cut ${index + 1}`);
          assert.deepStrictEqual(scene.originalTargets, allTargets, "rewind must retain the complete mission snapshot");
        }
      }
      assert.deepStrictEqual(errors, []);
      console.log(`PASS: ${path.basename(filename)} ${workflow} ${view}: first and two repeated replays reveal the correct targets at start and each cut`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

if (!process.argv[2] || !process.argv[3]) {
  throw new Error("Usage: node test_segment_replay_targets.js <url> <seg.json>");
}
check(process.argv[2], process.argv[3]).catch(e => { console.error(e); process.exitCode = 1; });
