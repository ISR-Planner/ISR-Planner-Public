// Actual preview source and request/accept flow, using DOM adapters from the
// acceptance suite. Solver responses are fixtures; NOAA data is the saved feed.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {harness} = require('./test_weather_accept');
const {assertOnlyRingClosure} = require('./test_weather_geometry');
const copy = x => JSON.parse(JSON.stringify(x));
const weather = JSON.parse(fs.readFileSync('webapp/_weather_diagnostics/noaa_20261012T012337Z.json'));
function playbackHarness(){
  const h=harness(),source=fs.readFileSync('webapp/isr.js','utf8');
  const start=source.indexOf('  // Animation speed slider and optional boost share one effective mission clock.');
  const end=source.indexOf('\n}\n',start);
  assert(start>=0&&end>start,'production animation speed controls');
  h.element('mc-speed-slider').value='2';
  h.element('mc-speed-boost').checked=false;
  h.element('mc-speed-boost-factor').value='100';
  vm.runInContext('const ANIMATION_SPEEDS = [.25,.5,1,2,4,8,16,32,64,128];\n'+source.slice(start,end),h.context);
  return h;
}
function fixture(h) {
  return {schema:'isr_weather_demo_v1',name:'Portable weather example',
    mission:{schema:'isr_env_v1',is_segmented:false,env:copy(h.state.env),drone_configs:copy(h.state.droneConfigs)},
    weather:copy(weather),window:{start:'2026-10-12T01:24:00.000Z',end:'2026-10-12T17:24:00.000Z',timezone:'America/Chicago'},
    bounds:[[20,-130],[60,-60]],policy:{safety_margin_km:5},playback:{speed:1280}};
}
(async()=>{
  // Legacy plain missions may contain coordinates saved from a repeated map
  // world. Import repairs them once; Fit and saved weather demos use that same
  // repaired mission and view, without changing the source file.
  const fit=harness(),legacy=fixture(fit).mission;
  for(const point of [...legacy.env.airports,...legacy.env.targets])point.lng-=360;
  legacy.map_view={center:{lat:37.5,lng:-455},zoom:4,
    bounds:{south:20,west:-490,north:60,east:-420}};
  const legacyBefore=JSON.stringify(legacy);
  fit.context.loadFromJson(legacy,'legacy-world.json');
  fit.input('wp-start','2026-10-12T01:24');fit.input('wp-end','2026-10-12T17:24');
  fit.element('wp-fit').click();
  assert.deepEqual(fit.mapFits.at(-1).points,[[35,-115],[40,-75],[40,-100]]);
  assert.equal(JSON.stringify(legacy),legacyBefore,'import leaves the source file unchanged');
  await fit.getWeather();
  const migratedDemo=fit.context.weatherPreview.exportDemo();
  assert.deepEqual(copy(migratedDemo.mission.map_view),{center:{lat:37.5,lng:-95},zoom:4,
    bounds:{south:20,west:-130,north:60,east:-60}});
  assert.deepEqual(copy(migratedDemo.mission.env.airports.map(p=>p.lng)),[-115,-75]);
  fit.context.loadFromJson(copy(migratedDemo),'migrated-demo.json');
  assert.equal(fit.context.weatherPreview.getMode(),'recorded');
  assert.deepEqual(copy(fit.context.weatherPreview.exportDemo().mission),copy(migratedDemo.mission));
  assert.equal(fit.element('wp-start').value,'2026-10-12T01:24');
  assert.equal(fit.element('wp-end').value,'2026-10-12T17:24');
  console.log('PASS legacy mission import, Weather Fit and demo export/re-import share the repaired mission and map view');

  const editing=harness();
  const committed=copy(editing.state.env);
  editing.state.editMode=true;
  editing.missionState.wasAnimating=true;
  editing.missionState.stagedEnv={airports:[{id:'A1',lat:51.47,lng:-.46},{id:'A2',lat:30.12,lng:31.4}],targets:[{id:'T1',lat:44,lng:26}],sams:[]};
  const staged=copy(editing.missionState.stagedEnv);
  editing.element('wp-fit').click();
  assert.deepEqual(editing.mapFits.at(-1).points,[[51.47,-.46],[30.12,31.4],[44,26]],'Fit uses the staged environment drawn on the canvas');
  assert.deepEqual(copy(editing.state.env),committed,'Fit must not commit edits');
  assert.deepEqual(copy(editing.missionState.stagedEnv),staged,'Fit must not move edited coordinates');
  editing.missionState.wasAnimating=false;
  editing.element('wp-fit').click();
  assert.deepEqual(editing.mapFits.at(-1).points,committed.airports.concat(committed.targets).map(p=>[p.lat,p.lng]));
  console.log('PASS Weather Fit uses current normal/live edits without committing or moving mission objects');

  const h=harness(),demo=fixture(h),originalFetch=h.context.fetch;let liveRequests=0;
  h.context.fetch=async(...args)=>{if(args[0].includes('/weather/current'))liveRequests++;return originalFetch(...args);};
  h.context.loadFromJson(copy(demo),'demo.json');
  assert.equal(h.context.weatherPreview.getMode(),'recorded');
  assert.equal(h.element('wp-start').value,'2026-10-11T20:24');
  assert.equal(h.element('mc-speed-boost').checked,true);
  assert.equal(h.element('mc-speed-slider').value,'9');
  assert.equal(h.element('wp-source-mode').textContent,'Recorded demo · original weather dates');
  assertOnlyRingClosure(await h.context.weatherDataSource.current(),weather);
  await h.getWeather();await h.solve();h.accept(true);assert.equal(liveRequests,0);
  const baseline=h.context.weatherPreview.exportDemo();
  h.state.checkpoint={active:true};h.state.animation.elapsedMissionSeconds=6000;
  h.state.env.airports.push({id:'C1-1',lat:38,lng:-110});
  h.state.droneConfigs['1'].start_airport='C1-1';
  await h.refreshAt(Date.parse('2026-10-12T03:04:00Z'));
  assert.equal(liveRequests,0);
  assert.deepEqual(copy(h.context.weatherPreview.exportDemo().mission),copy(baseline.mission));
  assert.deepEqual(copy(h.context.weatherPreview.exportDemo().window),copy(baseline.window));
  assert.equal(h.element('wp-start').value,'2026-10-11T22:04');
  h.state.animation.elapsedMissionSeconds=0;
  h.context.weatherPreview.resetToStart();
  assert.equal(h.element('wp-start').value,'2026-10-11T20:24');
  assert.equal(h.element('wp-time').value,0);
  assert.equal(h.context.buildSolveRequest().payload.env.weather_snapshot.start,Date.parse(demo.window.start));
  console.log('PASS portable recorded import, solve/accept, fresh checkpoint weather, and original baseline export/reset');

  // Malformed files cannot partially import a mission or replace weather.
  for(const mutate of [d=>{d.weather.cells[0].geometry.coordinates=null;},d=>{d.window.start='bad';},d=>{d.mission.env.airports[0].lat=null;},d=>{d.policy.safety_margin_km=0;}]){
    const broken=copy(demo);mutate(broken);
    const before=JSON.stringify(h.state.env);
    assert.throws(()=>h.context.weatherPreview.loadDemo(broken));
    assert.equal(JSON.stringify(h.state.env),before);assert.equal(h.context.weatherPreview.getMode(),'recorded');
  }
  console.log('PASS invalid demo data leaves loaded mission and weather intact');

  // Loading a saved effective multiplier must restore the matching controls,
  // including legacy 10× boosts, rather than changing speed to the new default.
  const playback=playbackHarness(),speedDemo=fixture(playback);
  const baseSpeeds=[.25,.5,1,2,4,8,16,32,64,128];
  for(const factor of [1,10,100])for(const [index,base] of baseSpeeds.entries()){
    const speed=base*factor;
    speedDemo.playback.speed=speed;
    playback.context.weatherPreview.loadDemo(copy(speedDemo),'speed-demo.json');
    assert.equal(playback.element('mc-speed-boost').checked,factor!==1,'boost at '+speed);
    assert.equal(playback.element('mc-speed-boost-factor').value,String(factor===1?100:factor));
    assert.equal(playback.element('mc-speed-slider').value,String(index));
    assert.equal(playback.state.animation.speedMultiplier,speed,'loaded effective speed');
    const saved=playback.context.weatherPreview.exportDemo();
    assert.equal(saved.playback.speed,speed,'saved effective speed');
    playback.context.weatherPreview.loadDemo(copy(saved),'roundtrip.json');
    assert.equal(playback.state.animation.speedMultiplier,speed,'round-trip effective speed');
  }
  delete speedDemo.playback;
  playback.context.weatherPreview.loadDemo(copy(speedDemo),'legacy-default.json');
  assert.equal(playback.state.animation.speedMultiplier,1280);
  assert.equal(playback.element('mc-speed-boost-factor').value,'10');
  for(const speed of [0,-1,3,256,25600,'12800',null]){
    const broken=copy(speedDemo);broken.playback={speed};
    assert.throws(()=>playback.context.weatherPreview.loadDemo(broken),/playback speed is unsupported/);
    assert.equal(playback.state.animation.speedMultiplier,1280,'invalid speed leaves playback unchanged');
  }
  console.log('PASS all unboosted, legacy 10× and new 100× demo speeds round-trip through actual controls; invalid speeds are rejected');

  // Source mode switch must remove old snapshot and use the live provider.
  h.element('wp-data-source').value='live';h.element('wp-data-source').dispatchEvent(new Event('change'));
  assert.equal(h.context.weatherPreview.getMode(),'live');
  assert.equal(h.context.buildSolveRequest().weatherApplied,false);
  assert(Math.abs(Date.parse(h.element('wp-start').value+'Z')-Date.now())<8*3600000);
  await h.context.weatherDataSource.current();assert.equal(liveRequests,1);
  h.context.loadFromJson(copy(demo),'demo.json');
  h.context.loadFromJson(copy(demo.mission),'plain-mission.json');
  assert.equal(h.context.weatherPreview.getMode(),'live');
  assert.equal(h.context.buildSolveRequest().weatherApplied,false);
  assert.throws(()=>h.context.weatherPreview.exportDemo(),/Load weather/);
  console.log('PASS Live NOAA source requests current feed; ordinary mission import clears recorded state');

  // Import/reset during an in-flight request must not install late weather.
  const pending=harness();let resolve;
  pending.context.fetch=()=>new Promise(r=>{resolve=r;});
  const retrieval=pending.getWeather();
  pending.context.loadFromJson(fixture(pending),'different-demo.json');
  resolve(new Response(JSON.stringify(weather)));await retrieval;
  assert.equal(pending.context.weatherPreview.getMode(),'recorded');
  assert.equal(pending.element('wp-start').value,'2026-10-11T20:24');
  assert.match(pending.element('wp-status').textContent,/Recorded demo loaded/);
  console.log('PASS a late live response cannot overwrite a newly imported recorded demo');

  const guarded=harness();await guarded.getWeather();await guarded.solve();guarded.accept(true);
  assert.equal(guarded.context.weatherPreview.allowAnimation(),true);
  await guarded.getWeather();assert.equal(guarded.context.weatherPreview.allowAnimation(),true);
  const guardInitialMission=copy(guarded.context.weatherPreview.exportDemo().mission);
  guarded.changeWeather(w=>{w.cells[0].name+=' updated';});
  // Update a cell guaranteed to be inside the selected area.
  const usedId=guarded.requests[0].env.weather_snapshot.advisories[0].id;
  guarded.changeWeather(w=>{w.cells.find(c=>c.id===usedId).name+=' revised';});
  await guarded.getWeather();
  assert.equal(guarded.context.weatherPreview.allowAnimation(),false);
  const mainSource=fs.readFileSync('webapp/isr.js','utf8');
  for(const name of ['startAnimation','resumeAnimation']){
    const start=mainSource.indexOf('function '+name+'(');let depth=0,end;
    for(let i=mainSource.indexOf(') {',start)+2;i<mainSource.length;i++){
      if(mainSource[i]==='{')depth++;
      if(mainSource[i]==='}'&&--depth===0){end=i+1;break;}
    }
    assert(end,'function extraction '+name);vm.runInContext(mainSource.slice(start,end),guarded.context);
  }
  const oldPause={sentinel:'kept'};guarded.missionState.pauseContext=oldPause;
  guarded.context.startAnimation(['1']);guarded.context.resumeAnimation();
  assert.equal(guarded.state.animation.active,false);
  assert.equal(guarded.missionState.pauseContext,oldPause);
  assert.match(guarded.element('mc-status-text').textContent,/Loaded weather differs/);
  guarded.context.weatherPreview.resetToStart();
  assert.equal(guarded.context.weatherPreview.allowAnimation(),true);
  guarded.context.loadFromJson(guardInitialMission,'repeat.json');
  await guarded.getWeather();await guarded.solve();guarded.context.acceptSolution();
  assert.equal(guarded.context.weatherPreview.allowAnimation(),true);
  console.log('PASS start/resume allow unchanged weather, refuse changed weather, and recover through Reset or fresh import/solve/accept');

  const demoPath=process.argv[2];
  if(demoPath){
    const fileHarness=harness(),actual=JSON.parse(fs.readFileSync(demoPath,'utf8'));
    fileHarness.context.weatherPreview.loadDemo(actual,'office-demo.json');
    assert.equal(fileHarness.context.weatherPreview.getMode(),'recorded');
    assert.deepEqual(copy(fileHarness.state.env),actual.mission.env);
    assertOnlyRingClosure(fileHarness.context.weatherPreview.exportDemo().weather,actual.weather);
    await fileHarness.solve();fileHarness.accept(true);
    assert.equal(fileHarness.context.weatherPreview.allowAnimation(),true);
    console.log('PASS actual office demo bundle validates, imports, round-trips capture, and accepts its fixture solution');
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
