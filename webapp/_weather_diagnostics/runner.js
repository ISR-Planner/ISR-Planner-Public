/* Local diagnostic only: the imported mission stays in memory. Weather comes
 * from a public NOAA capture, never silently from the current feed. */
window.PageCapabilities.register('weatherDiagnostics', () => {
 const panel=document.createElement('section');
 panel.style.cssText='position:fixed;top:0;left:0;right:0;z-index:10000;background:#111827;color:white;padding:10px;font:13px system-ui;max-height:40vh;overflow:auto;border-bottom:2px solid #22c55e';
 panel.innerHTML='<b>Automated speed diagnostic · saved NOAA replay</b> <button id="diag-run">Run comparisons</button> <button id="diag-background">Run background sample</button><div>Recorded NOAA · Oct 11, 2026, 8:23 PM CDT / Oct 12, 01:23 UTC · historical test data</div><div id="diag-status">Import the mission using the normal Import button.</div><table id="diag-table"><thead><tr><th>Case</th><th>Requested</th><th>Wall seconds</th><th>Mission seconds</th><th>Effective ×</th><th>Frames</th><th>Lost frame seconds</th><th>Clock/distance error km</th></tr></thead><tbody></tbody></table><script id="diag-results" type="application/json">[]</script>';
 document.body.append(panel);document.body.style.paddingTop='120px';
 let mission,filename,frozenWeather,frozenSolution,running=false,measure=null,rows=[],weatherWindowApplied=false,portableDemoLoaded=false;
 const realFetch=window.fetch.bind(window), realRAF=window.requestAnimationFrame.bind(window);
 const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 const status=t=>{document.getElementById('diag-status').textContent=t;};
 const jsonCopy=x=>JSON.parse(JSON.stringify(x));
 async function recordedWeather(){
  if(!frozenWeather){
   const response=await realFetch('/static/_weather_diagnostics/noaa_20261012T012337Z.json',{cache:'no-store'});
   if(!response.ok)throw Error('Saved NOAA capture could not be loaded: '+response.status);
   const capture=await response.json();
   if(!Array.isArray(capture.cells)||!Number.isFinite(Date.parse(capture.retrievedAt)))throw Error('Saved NOAA capture is invalid.');
   frozenWeather=capture;
  }
  if(!portableDemoLoaded)window.weatherPreview?.useRecordedCapture(frozenWeather);
  return frozenWeather;
 }
 function applyRecordedWindow(){
  const zone=document.getElementById('wp-zone');zone.value='UTC';zone.dispatchEvent(new Event('change'));
  const a=Math.ceil(Date.parse(frozenWeather.retrievedAt)/60000)*60000;
  document.getElementById('wp-start').value=new Date(a).toISOString().slice(0,16);document.getElementById('wp-end').value=new Date(a+16*3600000).toISOString().slice(0,16);document.getElementById('wp-start').dispatchEvent(new Event('input'));document.getElementById('wp-end').dispatchEvent(new Event('input'));
  weatherWindowApplied=true;
 }
 window.fetch=async(input,init)=>{
  const url=typeof input==='string'?input:input.url;
  if(url.includes('/api/weather/current')){
   return new Response(JSON.stringify(await recordedWeather()),{headers:{'Content-Type':'application/json'}});
  }
  if(url.includes('/api/solve_with_allocation')){
   if(!running)return realFetch(input,init);
   if(!frozenSolution){const response=await realFetch(input,init);const data=await response.clone().json();if(data.success)frozenSolution=data;return response;}
   return new Response(JSON.stringify(frozenSolution),{headers:{'Content-Type':'application/json'}});
  }
  return realFetch(input,init);
 };
 window.requestAnimationFrame=fn=>realRAF(ts=>{
  if(measure&&fn.name==='animate'){
   if(measure.lastTs!==null){const gap=ts-measure.lastTs;measure.rawGapMs+=gap;measure.gaps.push(gap);}
   measure.lastTs=ts;measure.frames++;
  }
  fn(ts);
 });
 const originalLoad=loadFromJson;
 loadFromJson=(data,name)=>{
  if(name&&name!=='Import your mission JSON'&&!running){mission=jsonCopy(data);filename=name;frozenSolution=null;weatherWindowApplied=false;status('Loaded '+name+'. Ready to replay saved NOAA and run comparisons.');}
  const result=originalLoad(data,name);
  portableDemoLoaded=data?.schema==='isr_weather_demo_v1';
  if(!portableDemoLoaded)window.weatherPreview?.useRecordedCapture(null);
  const source=document.getElementById('wp-data-source');if(source){source.value='recorded';source.disabled=true;source.title='This diagnostic page uses recorded weather only. Open Weather Preview for Live NOAA.';}
  return result;
 };
 const originalGetWeather=document.getElementById('wp-get').onclick;
 document.getElementById('wp-get').onclick=async()=>{
  try{
   if(!portableDemoLoaded){await recordedWeather();if(!weatherWindowApplied)applyRecordedWindow();}
   await originalGetWeather();
   const button=document.getElementById('wp-get');
   if(button.textContent.startsWith('✓'))button.textContent='✓ Recorded NOAA loaded';
   status(portableDemoLoaded?'Using the loaded demo weather. No live weather is requested.':'Using recorded NOAA from Oct 11, 8:23 PM CDT / Oct 12, 01:23 UTC. Get forecast reuses this capture.');
  }catch(error){status(error.message);throw error;}
 };
 function setSpeed(value){
  document.getElementById('mc-speed-boost').checked=false;
  const input=document.getElementById('mc-speed-slider');input.value=ANIMATION_SPEEDS.indexOf(value);input.dispatchEvent(new Event('input',{bubbles:true}));
 }
 function render(){
  const body=document.querySelector('#diag-table tbody');body.replaceChildren();
  for(const row of rows){const tr=document.createElement('tr');for(const value of [row.case,row.requested,row.wallSeconds.toFixed(2),row.missionSeconds.toFixed(2),row.effectiveMultiplier.toFixed(2),row.frames,row.lostFrameSeconds.toFixed(2),row.clockDistanceErrorKm.toFixed(4)]){const td=document.createElement('td');td.textContent=value;td.style.padding='3px 10px';tr.append(td);}body.append(tr);}
  document.getElementById('diag-results').textContent=JSON.stringify({mission:filename,weatherMode:'recorded NOAA',noaaRetrievedAt:frozenWeather?.retrievedAt,rows},null,2);
 }
 async function prepare(){
  stopAnimation();resetMission();originalLoad(jsonCopy(mission),filename);
  document.getElementById('wp-auto-checkpoint').checked=false;
  document.getElementById('wp-fit').click();await pause(300);
  if(!portableDemoLoaded){await recordedWeather();applyRecordedWindow();}
  await document.getElementById('wp-get').onclick();
  const result=await runPlanner();if(!result?.success)throw Error('Diagnostic solve failed: '+JSON.stringify(result?.errors));
  acceptSolution();if(missionState.mode!==MissionMode.READY_TO_ANIMATE)throw Error('Diagnostic draft not accepted: '+document.getElementById('wp-status').textContent);
 }
 async function sample(speed,label,duration=3000,hiddenOverlay=false){
  await prepare();
  for(const id of ['wp-no-fly','wp-other']){document.getElementById(id).checked=!hiddenOverlay;document.getElementById(id).dispatchEvent(new Event('change'));}
  setSpeed(speed);measure={frames:0,lastTs:null,rawGapMs:0,gaps:[]};
  startAnimation(['1']);const begin=performance.now(),origin=state.animation.elapsedMissionSeconds||0;
  const d=state.animation.drones['1'],initial=d.distanceTraveled,speedKmMin=state.droneConfigs['1'].speed;
  const initialHoldMinutes=d._elapsedHoldMinutes||0;
  await pause(duration);const wallSeconds=(performance.now()-begin)/1000,missionSeconds=state.animation.elapsedMissionSeconds-origin;
  const flown=d.distanceTraveled-initial;
  const holdMinutes=(d._elapsedHoldMinutes||0)-initialHoldMinutes;
  const row={case:label,requested:speed,wallSeconds,missionSeconds,effectiveMultiplier:missionSeconds/wallSeconds,frames:measure.frames,lostFrameSeconds:Math.max(0,measure.rawGapMs/1000-missionSeconds/speed),rawFrameSeconds:measure.rawGapMs/1000,maxFrameMs:Math.max(0,...measure.gaps),distanceKm:flown,holdMinutes,clockDistanceErrorKm:flown-Math.max(0,missionSeconds/60-holdMinutes)*speedKmMin};
  stopAnimation();measure=null;rows.push(row);render();return row;
 }
 document.getElementById('diag-run').onclick=async()=>{
  if(running)return;if(!mission){status('Import the mission JSON first, then click Run comparisons.');return;}running=true;rows=[];document.getElementById('diag-run').disabled=true;
  const savedControls=Object.fromEntries(['wp-auto-checkpoint','wp-no-fly','wp-other','mc-speed-boost'].map(id=>[id,document.getElementById(id).checked]));
  try{for(const speed of [1,4,16,64,128]){status('Testing '+speed+'× with recorded weather…');await sample(speed,'normal rendering');}
    status('Testing 128× with weather polygons hidden…');await sample(128,'weather overlay hidden',3000,true);status('Foreground comparisons complete. Background test is ready.');}
  catch(error){status(error.stack||error.message);}finally{stopAnimation();measure=null;for(const [id,checked] of Object.entries(savedControls)){document.getElementById(id).checked=checked;document.getElementById(id).dispatchEvent(new Event('change'));}running=false;document.getElementById('diag-run').disabled=false;}
 };
 document.getElementById('diag-background').onclick=async()=>{
  if(!mission||running)return;running=true;
  const savedControls=Object.fromEntries(['wp-auto-checkpoint','wp-no-fly','wp-other','mc-speed-boost'].map(id=>[id,document.getElementById(id).checked]));
  try{await prepare();status('BACKGROUND SAMPLE READY · switch to another Chrome tab now. Sampling starts in 2 seconds.');await pause(2000);
   setSpeed(128);measure={frames:0,lastTs:null,rawGapMs:0,gaps:[]};startAnimation(['1']);
   const begin=performance.now(),origin=state.animation.elapsedMissionSeconds||0,d=state.animation.drones['1'],initial=d.distanceTraveled,initialHold=d._elapsedHoldMinutes||0;
   await pause(8000);
   const wallSeconds=(performance.now()-begin)/1000,missionSeconds=state.animation.elapsedMissionSeconds-origin,flown=d.distanceTraveled-initial,holdMinutes=(d._elapsedHoldMinutes||0)-initialHold;
   rows.push({case:'background tab',requested:128,wallSeconds,missionSeconds,effectiveMultiplier:missionSeconds/wallSeconds,frames:measure.frames,lostFrameSeconds:Math.max(0,measure.rawGapMs/1000-missionSeconds/128),rawFrameSeconds:measure.rawGapMs/1000,maxFrameMs:Math.max(0,...measure.gaps),distanceKm:flown,holdMinutes,clockDistanceErrorKm:flown-Math.max(0,missionSeconds/60-holdMinutes)*state.droneConfigs['1'].speed});stopAnimation();measure=null;render();status('All comparisons complete.');
  }catch(error){status(error.stack||error.message);}finally{stopAnimation();measure=null;for(const [id,checked] of Object.entries(savedControls)){document.getElementById(id).checked=checked;document.getElementById(id).dispatchEvent(new Event('change'));}running=false;}
 };
});
