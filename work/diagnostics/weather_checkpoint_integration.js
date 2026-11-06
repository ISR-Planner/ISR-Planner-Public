// Direct Python backend + actual JS playback/pause/cut engine, without Chrome.
// User mission data stays in this process and is never written to a served file.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const mission=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const data=JSON.parse(fs.readFileSync('webapp/_weather_diagnostics/noaa_20261012T012337Z.json','utf8'));
const source=fs.readFileSync('webapp/isr.js','utf8');
const geometry=fs.readFileSync('webapp/latlng_utils.js','utf8');
const plain=x=>JSON.parse(JSON.stringify(x));
function extract(text,name){
 const start=text.indexOf('function '+name+'(');assert(start>=0,name);
 let depth=0;for(let i=text.indexOf(') {',start)+2;i<text.length;i++){
  if(text[i]==='{')depth++;
  if(text[i]==='}'&&--depth===0)return text.slice(start,i+1);
 }throw Error('Unbalanced '+name);
}
function backend(operation,args){
 return JSON.parse(execFileSync('venv/bin/python',['-m','work.diagnostics.weather_backend_worker'],
  {input:JSON.stringify({operation,arguments:args})+'\n',maxBuffer:20*1024*1024,encoding:'utf8',timeout:60000}));
}
const origin=Math.ceil(Date.parse(data.retrievedAt)/60000)*60000,end=origin+16*3600000;
const points=[...mission.env.airports,...mission.env.targets];
const bounds=[Math.min(...points.map(p=>p.lng))-5,Math.min(...points.map(p=>p.lat))-5,
 Math.max(...points.map(p=>p.lng))+5,Math.max(...points.map(p=>p.lat))+5];
const areaCells=data.cells.filter(c=>c.box[2]>=bounds[0]&&c.box[0]<=bounds[2]&&c.box[3]>=bounds[1]&&c.box[1]<=bounds[3]);
function selectedCells(start){return areaCells.filter(c=>c.validAt?Date.parse(c.validAt)>=start&&Date.parse(c.validAt)<=end:Date.parse(c.validFrom)<end&&Date.parse(c.validTo)>start);}
function weatherEnv(env,start){
 const copy=plain(env),cells=selectedCells(start);
 copy.sams=[...(copy.sams||[]).filter(s=>!s.weather_ring),...cells.filter(c=>c.rule==='No fly').flatMap(c=>
  (c.geometry.type==='Polygon'?[c.geometry.coordinates]:c.geometry.coordinates).map((rings,i)=>({
   id:'WX-'+c.id+'-'+i,center:{lat:rings[0][0][1],lng:rings[0][0][0]},range:0,range_km:0,weather_ring:rings[0].map(p=>p.slice(0,2))})))];
 copy.weather_snapshot={retrievedAt:data.retrievedAt,start,end,advisories:cells};return copy;
}
const initial=backend('solve',{env:weatherEnv(mission.env,origin),drone_configs:mission.drone_configs,
 allocation_strategy:'geographic',use_sam_aware_distances:true,post_optimize:true});
assert(initial.success,JSON.stringify(initial.errors));
const route=initial.routes['1'];assert(!route.route.includes('T1')&&!route.route.includes('T4'));
if(process.argv.includes('--inspect')){
 const geometryContext=vm.createContext({});vm.runInContext(geometry.match(/const EARTH_RADIUS_KM = [^;]+;/)[0]+'\n'+extract(geometry,'haversineKm'),geometryContext);
 const cumulative=[0];for(let i=1;i<route.trajectory.length;i++)cumulative.push(cumulative.at(-1)+geometryContext.haversineKm(...route.trajectory[i-1],...route.trajectory[i]));
 console.log(JSON.stringify({route:route.route,distance:route.distance,frameTotal:route.anim_frame.total_distance,geometryTotal:cumulative.at(-1),
  frameKeys:Object.keys(route.anim_frame),targetVertices:mission.env.targets.filter(t=>route.route.includes(t.id)).map(t=>{
   let best=Infinity,index=0;route.trajectory.forEach((p,i)=>{const d=geometryContext.haversineKm(p[0],p[1],t.lat,t.lng);if(d<best){best=d;index=i;}});
   return {id:t.id,vertexDistance:cumulative[index],offTarget:best};
  })},null,2));process.exit(0);
}
// Both recorded polygons restricting T1/T4 end at this published boundary.
const boundary=Date.parse('2026-10-12T02:55:00Z');
const helperStart=source.indexOf('const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND');
const helpers=source.slice(helperStart,source.indexOf('// Killed drones',helperStart));
const cutStart=source.indexOf('async function prepareCutFromCurrentState()');
const cut=source.slice(cutStart,source.indexOf('\n/**',cutStart));
const resumeStart=source.indexOf('function resumeAnimation()');
const resume=source.slice(resumeStart,source.indexOf('\n/**',resumeStart));
const rows=[];
async function scenario(pattern){
 let callback,solves=0,cutCount=0,review,replanFailure;const elements={};
 const elapsed=(boundary-origin)/1000-1, speed=mission.drone_configs['1'].speed;
 const distance=elapsed/60*speed;
 const drone={animating:true,distanceTraveled:distance,totalDistance:route.anim_frame.total_distance,
  cumulativeDistances:route.anim_frame.cumulative_distances,legBurnedDistance:distance};
 const context={window:{},document:{getElementById:id=>elements[id]||(elements[id]={checked:true,textContent:''})},
 state:{env:plain(mission.env),initialEnvSnapshot:plain(mission.env),droneConfigs:plain(mission.drone_configs),
  routes:{'1':{...plain(route),trajectory:plain(route.anim_frame.trajectory),
   waypoint_indices:plain(route.anim_frame.waypoint_indices)}},visitedTargets:[],legFuel:{'1':distance},legFuelHistory:{},
  animation:{active:false,speedMultiplier:1,elapsedMissionSeconds:elapsed,missionDistance:distance,drones:{'1':drone}}},
 missionState:{pauseContext:{droneStates:{'1':drone},animatingDrones:['1']}},
 MissionMode:{ANIMATING:'ANIMATING',PAUSED_MID_ANIMATION:'PAUSED',CHECKPOINT:'CHECKPOINT'},
 missionReplay:{getSegmentCount:()=>1,getCurrentSegmentIndex:()=>0,getSegment:()=>({env:mission.env,solution:initial})},segmentedImport:{isActive:()=>false},Date,JSON,setInterval:()=>{},
 requestAnimationFrame:fn=>{callback=fn;return 1;},cancelAnimationFrame:()=>{},
 appendDebugLine:()=>{},drawEnvironment:()=>{},requestDraw:()=>{},updateStatsFromAnimation:()=>{},
 updateAnimationButtonStates:()=>{},updateEnvPanelLocked:()=>{},updateCanvasPointerEvents:()=>{},setAddMode:()=>{},
 getUiPermissions:()=>({canPause:true}),setMissionMode:mode=>{context.missionState.mode=mode;},
 readDroneConfigsFromUI:()=>plain(context.state.droneConfigs),
 markTargetVisitedByDrone:id=>{if(!context.state.visitedTargets.includes(id))context.state.visitedTargets.push(id);},
 fetch:async(url,init)=>({ok:true,json:async()=>url.includes('/impact')?backend('impact',JSON.parse(init.body)):data})};
 // Backend signature differs only in the names used by the HTTP request model.
 const rawFetch=context.fetch;
 context.fetch=async(url,init)=>{
  if(url.includes('/impact')){
   const p=JSON.parse(init.body);const result=backend('impact',{env:p.env,remaining_routes:p.remaining_routes,remaining_legs:p.remaining_legs,
    missed_targets:p.missed_targets,previous:p.previous,current:p.current,start:p.start,end:p.end,retrieved_at:p.retrieved_at});
   return {ok:true,json:async()=>result};
  }return rawFetch(url,init);
 };
 vm.createContext(context);
 vm.runInContext(geometry.match(/const EARTH_RADIUS_KM = [^;]+;/)[0]+'\nconst TARGET_HIT_KM=.05;\n'+
  extract(geometry,'haversineKm')+'\n'+extract(geometry,'interpolateFlightPosition')+'\n'+extract(geometry,'isTargetVisited')+'\n'+
  ['interpolateLongitude','split_polyline_at_distance','targetHitToleranceKm','getAllKnownTargets','pauseAnimation','applyResumeNodeToDroneConfig','buildCheckpointEnv','buildCheckpointRepairRoutes'].map(n=>extract(source,n)).join('\n')+
  '\n'+helpers+'\n'+cut+'\n'+resume,context);
 const realCut=context.prepareCutFromCurrentState;
 context.prepareCutFromCurrentState=async()=>{cutCount++;return realCut();};
 vm.runInContext(fs.readFileSync('webapp/weather_checkpoints.js','utf8'),context);
 const controller=context.window.weatherCheckpoints;
 controller.configure({snapshot:()=>({dirty:false}),epoch:()=>origin+context.state.animation.elapsedMissionSeconds*1000,end:()=>end,
  data:()=>data,refreshAt:async()=>{},recordReview:r=>{review=r;},
  refreshAndReplan:async epoch=>{
   try{
   solves++;const request=context.buildCheckpointEnv(),env=weatherEnv(request.env,epoch);
   const configs=request.droneConfigs;
   const result=backend('solve',{env,drone_configs:configs,visited_targets:context.state.visitedTargets,
    is_checkpoint_replan:request.isCheckpointReplan,repair_routes:context.buildCheckpointRepairRoutes(configs),allocation_strategy:'geographic',use_sam_aware_distances:true,post_optimize:true});
   assert(result.success,JSON.stringify(result.errors));context.missionState.draftSolution=result;
   controller.arm(selectedCells(epoch));return true;
   }catch(error){replanFailure=error;throw error;}
  }});
 controller.arm(selectedCells(origin));context.resumeAnimation();callback(0);
 for(let tick=1;tick<=100&&context.state.animation.active;tick++){
  context.state.animation.speedMultiplier=pattern[(tick-1)%pattern.length];callback(tick*100);
  await controller.inspect();
 }
 assert.equal(cutCount,1);assert.equal(solves,1);assert.equal(context.state.animation.active,false);
 if(replanFailure)throw replanFailure;
 assert.equal(context.state.pendingCheckpoints['1'].id,'C1-1');
 assert(Math.abs(context.state.animation.elapsedMissionSeconds-(boundary-origin)/1000)<1e-7);
 const flown=context.state.legFuel['1'];
 assert.deepEqual(context.state.visitedTargets,['T8']);
 assert(Math.abs(context.state.droneConfigs['1'].start_fuel-(mission.drone_configs['1'].fuel_budget-flown))<1e-7);
 assert.equal(context.state.droneConfigs['1'].fuel_budget,mission.drone_configs['1'].fuel_budget);
 const revised=context.missionState.draftSolution.routes['1'].route;
 assert.equal(revised[0],context.state.pendingCheckpoints['1'].id);
 assert(revised.includes('T1')&&revised.includes('T4'),JSON.stringify(revised));
 assert(!revised.includes('T8'));
 assert(review.requiresWeatherReview);assert.match(elements['wp-checkpoint-status'].textContent,/Weather review required/);
 await controller.inspect(true);assert.equal(cutCount,1);assert.equal(solves,1);
 rows.push({multipliers:pattern,cut:context.state.pendingCheckpoints['1'].id,missionTime:new Date(boundary).toISOString(),
  cuts:cutCount,freshSolves:solves,visitedPreserved:context.state.visitedTargets,route:revised,
  fuelPreserved:true,weatherReviewRequired:review.requiresWeatherReview,stillPaused:!context.state.animation.active});
}
(async()=>{for(const pattern of [[1],[4],[16],[64],[128],[1280],[12800],[1,12800,4]])await scenario(pattern);
 console.log(JSON.stringify({initialRoute:route.route,checks:rows},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
