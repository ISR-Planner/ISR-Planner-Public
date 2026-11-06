const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
async function scenario(canReplan){
 let cuts=0,solves=0,refreshes=0, timers=[], text={},recordedReview;
 const context={window:{}, document:{getElementById:id=>text[id]||(text[id]={checked:true,textContent:''})},
   state:{animation:{active:true,drones:{'1':{animating:true,distanceTraveled:1,totalDistance:100}}},routes:{'1':{trajectory:[[32,-98],[32,-96]],route:['A1','A2']}},visitedTargets:[],env:{targets:[{id:'T1'}]}},
   split_polyline_at_distance:()=>({suffixPoints:[[32,-98],[32,-96]]}),
   pauseAnimation:()=>{context.state.animation.active=false;},
   prepareCutFromCurrentState:async()=>{cuts++;},drawEnvironment:()=>{},appendDebugLine:()=>{},
   setInterval:fn=>{timers.push(fn);},Date,JSON,
   fetch:async url=>({ok:true,json:async()=>url.includes('/impact')?{affected:true,canReplan,reasons:['test change'],notices:canReplan?['Expired advisories do not confirm clearance.']:[],requiresWeatherReview:canReplan,recoveredTargets:canReplan?['T1']:[]}:{cells:[],retrievedAt:new Date().toISOString()}})};
 vm.createContext(context);vm.runInContext(fs.readFileSync('webapp/weather_checkpoints.js','utf8'),context);
 const controller=context.window.weatherCheckpoints;
 controller.configure({snapshot:()=>({cells:[],dirty:false}),epoch:()=>Date.now(),end:()=>Date.now()+3600000,data:()=>({cells:[],retrievedAt:new Date().toISOString()}),recordReview:review=>{recordedReview=review;},refreshAt:async()=>{refreshes++;},refreshAndReplan:async()=>{refreshes++;solves++;return true;},replan:async()=>{solves++;return true;}});
 controller.arm([]);await controller.inspect(true);
 assert.equal(cuts,1);assert.equal(context.state.animation.active,false);
 if(canReplan){assert.equal(solves,1);assert.match(text['wp-checkpoint-status'].textContent,/Review, Accept/);assert.match(text['wp-checkpoint-status'].textContent,/Weather review required/);assert(recordedReview.requiresWeatherReview);assert.deepEqual(Array.from(recordedReview.recoveredTargets),['T1']);}
 else{assert.equal(solves,0);assert.equal(await controller.allowSolve(),false);assert.equal(solves,0);}
 await controller.inspect(true);assert.equal(cuts,1);
 assert(refreshes>0);
}
(async()=>{await scenario(true);await scenario(false);console.log('Checkpoint refresh, review, hold and duplicate prevention pass.');})();
async function speedInvariant(pattern){
 const base=Date.parse('2026-10-12T02:00:00Z'), elements={};
 const context={window:{},document:{getElementById:id=>elements[id]||(elements[id]={checked:true,textContent:''})},
 state:{animation:{active:true,speedMultiplier:1,elapsedMissionSeconds:0,drones:{'1':{animating:true,distanceTraveled:0,totalDistance:20}}},droneConfigs:{'1':{speed:10}},routes:{'1':{route:['A1','A2'],trajectory:[[32,-98],[32,-96]]}},env:{targets:[]},visitedTargets:[]},
 Date,JSON,setInterval:()=>{},split_polyline_at_distance:()=>({suffixPoints:[[32,-98],[32,-96]]}),
 pauseAnimation:()=>{throw Error('An unaffected weather transition must not cut');},prepareCutFromCurrentState:()=>{},drawEnvironment:()=>{},appendDebugLine:()=>{},
 fetch:async url=>({ok:true,json:async()=>url.includes('/impact')?{affected:false,canReplan:true}:{cells:[],retrievedAt:new Date(base).toISOString()}})};
 vm.createContext(context);vm.runInContext(fs.readFileSync('webapp/weather_checkpoints.js','utf8'),context);
 const source=fs.readFileSync('webapp/isr.js','utf8');vm.runInContext(source.slice(source.indexOf('const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND'),source.indexOf('// Killed drones',source.indexOf('const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND'))),context);
 const controller=context.window.weatherCheckpoints;
 controller.configure({snapshot:()=>({}),epoch:()=>base+context.state.animation.elapsedMissionSeconds*1000,end:()=>base+180000});
 controller.arm([{rule:'No fly',validTo:new Date(base+60000).toISOString()}]);
 let checked=false;
 for(let tick=0;tick<20000&&context.state.animation.drones['1'].distanceTraveled<20-1e-9;tick++){
  context.state.animation.speedMultiplier=pattern[tick%pattern.length];
  const minutes=vm.runInContext('advanceMissionClock(0.1)',context);
  context.state.animation.drones['1'].distanceTraveled+=minutes*10;
  if(!checked&&context.state.animation.elapsedMissionSeconds>=60-1e-9){
   assert(Math.abs(context.state.animation.elapsedMissionSeconds-60)<1e-7);
   assert.equal(controller.limitStep(100),0);
   await controller.inspect(true);checked=true;
  }
 }
 assert(checked);assert(Math.abs(context.state.animation.elapsedMissionSeconds-120)<1e-7);
}
(async()=>{for(const pattern of [[1],[4],[128],[1,128,4]])await speedInvariant(pattern);console.log('Weather transition occurs at 60 mission seconds at every playback speed.');})();
