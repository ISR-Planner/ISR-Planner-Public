// NODE_PATH=<playwright modules> node this-file <url> <seg.json>
// Uses the real Cesium viewer and the shipped animation, with no seeking.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { chromium } = require("playwright");

async function check(url, filename) {
  const data = JSON.parse(fs.readFileSync(filename, "utf8"));
  const browser = await chromium.launch({ headless: true,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    page.on("dialog", d => d.dismiss());
    await page.goto(url);
    await page.waitForFunction(() => typeof Cesium !== "undefined" &&
      typeof loadSegmentedMissionFromJson === "function");
    await page.evaluate(data => {
      // Observe the actual rendered entities without adding production test hooks.
      window.replay3dEntities = {};
      const add = Cesium.EntityCollection.prototype.add;
      Cesium.EntityCollection.prototype.add = function(spec) {
        const entity = add.call(this, spec);
        if (/^D\d+ route$/.test(entity.name)) window.replay3dEntities[entity.name] = entity;
        return entity;
      };
      loadSegmentedMissionFromJson(data, "3d-replay-regression.json");
    }, data);
    await page.click("#btn-view-3d");
    await page.waitForFunction(() => cesiumView.isActive() &&
      cesiumView._entityNames().includes("D1 route"), null, { timeout: 60000 });
    const result = await page.evaluate(() => {
      let callback;
      window.requestAnimationFrame = fn => { callback = fn; return 1; };
      window.cancelAnimationFrame = () => {};
      const snapshot = () => {
        const names = cesiumView._entityNames();
        const routes = {};
        Object.entries(state.routes).forEach(([did, rd]) => {
          if (!rd.trajectory || rd.trajectory.length < 2) return;
          const entity = window.replay3dEntities[`D${did} route`];
          if (!entity || !names.includes(entity.name)) throw new Error(`Missing D${did} scene route`);
          const positions = entity.polyline.positions.getValue(Cesium.JulianDate.now());
          const coordinates = positions.map(p => {
            const c = Cesium.Cartographic.fromCartesian(p);
            return [Cesium.Math.toDegrees(c.latitude), Cesium.Math.toDegrees(c.longitude)];
          });
          // Geodesic densification preserves every original trajectory vertex.
          // A stale final route cannot match the original or an intermediate solve.
          let mismatchKm = 0;
          rd.trajectory.forEach(point => {
            let nearest = Infinity;
            coordinates.forEach(p => {
              nearest = Math.min(nearest, haversineKm(...point, ...p));
            });
            mismatchKm = Math.max(mismatchKm, nearest);
          });
          routes[did] = { mismatchKm, route: rd.route.slice() };
        });
        return {
          segment: missionReplay.getCurrentSegmentIndex(),
          importSegment: segmentedImport.getCurrentSegmentIndex(),
          sceneTargets: names.filter(n => /^T\d+$/.test(n)).sort(),
          envTargets: (state.env.targets || []).map(t => t.id).sort(),
          sceneMarkers: names.filter(n => /^C\d+ D/.test(n)).sort(),
          stateMarkers: [...collectCheckpointMarkers().values()]
            .map(m => `${m.label} D${m.did}`).sort(),
          routes,
        };
      };
      const runs = [];
      // First import, repeat after completion, continuous replay, then switching
      // back to segment replay must all refresh the scene at the start.
      for (const segmented of [true, true, false, true]) {
        document.getElementById("mc-replay-segments").checked = segmented;
        const cameraBefore = cesiumView._camHeight();
        startAnimation();
        const cameraAfter = cesiumView._camHeight();
        state.animation.speedMultiplier = 8;
        const start = snapshot(), cuts = [];
        let previousSegment = start.segment;
        for (let tick = 0; tick < 30000; tick++) {
          callback(tick * 100);
          const currentSegment = missionReplay.getCurrentSegmentIndex();
          if (currentSegment !== previousSegment) {
            cuts.push(snapshot());
            previousSegment = currentSegment;
            // Skip only the checkpoint's wall-clock presentation pause.
            state.animation.checkpointPause = null;
          }
          if (state.animation.missionComplete) break;
        }
        runs.push({ segmented, cameraBefore, cameraAfter, start, cuts,
          complete: state.animation.missionComplete });
      }
      return runs;
    });
    for (const run of result) {
      assert(run.complete, "each replay must finish");
      assert.strictEqual(run.cameraAfter, run.cameraBefore, "starting replay must preserve the camera");
      assert.strictEqual(run.start.segment, 0);
      assert.deepStrictEqual(run.start.sceneMarkers, run.start.stateMarkers);
      if (run.segmented) {
        assert.deepStrictEqual(run.start.sceneMarkers, [], "future diamonds must be hidden at replay start");
        assert.strictEqual(run.cuts.length, data.segments.length - 1);
        for (const [did, route] of Object.entries(run.start.routes)) {
          assert.deepStrictEqual(route.route, data.segments[0].routes[did].route);
        }
      } else {
        assert.strictEqual(run.cuts.length, 0, "continuous replay must not switch segments");
        assert(run.start.sceneMarkers.length > 0, "continuous replay shows the full cut history");
      }
      for (const scene of [run.start, ...run.cuts]) {
        assert.deepStrictEqual(scene.sceneMarkers, scene.stateMarkers,
          `3D diamonds must match 2D at segment ${scene.segment}`);
        for (const [did, route] of Object.entries(scene.routes)) {
          assert(route.mismatchKm < 0.001,
            `D${did} has a stale 3D route at segment ${scene.segment}: ${route.mismatchKm} km`);
        }
        if (run.segmented) {
          const expectedTargets = [...new Set(data.segments.slice(0, scene.segment + 1)
            .flatMap(s => s.env.targets.map(t => t.id)))].sort();
          assert.strictEqual(scene.importSegment, scene.segment, "environment and route indices must agree");
          assert.deepStrictEqual(scene.envTargets, expectedTargets);
          assert.deepStrictEqual(scene.sceneTargets, expectedTargets,
            `3D must reveal targets only through segment ${scene.segment}`);
          assert(!scene.sceneMarkers.some(n => Number(n.match(/^C(\d+)/)[1]) > scene.segment),
            "later cuts must stay hidden until reached");
        }
      }
    }
    assert.deepStrictEqual(errors, []);
    console.log(`PASS: ${path.basename(filename)}: real 3D routes, targets and diamonds match playback at start and every cut; repeated replay, mode changes and camera preservation verified`);
  } finally {
    await browser.close();
  }
}

if (!process.argv[2] || !process.argv[3]) {
  throw new Error("Usage: node test_segment_3d_replay.js <url> <seg.json>");
}
check(process.argv[2], process.argv[3]).catch(e => { console.error(e); process.exitCode = 1; });
