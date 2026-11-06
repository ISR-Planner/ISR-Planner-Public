/* Weather-only controller. Cuts and review use the planner's existing workflow. */
window.weatherCheckpoints=(()=>{
  let config, baseline=[], busy=false, lastPoll=0, lastBoundary='', armed=false, pending=null, transitionDue=false, readyReview=null, generation=0;
  const status=text=>{document.getElementById('wp-checkpoint-status').textContent=text;};
  function draftReady(solved,review){
    const warning=review.notices?.length?' Weather review required: '+review.notices.join(' '):'';
    if(solved)config.recordReview?.(review);
    readyReview=null;
    status((solved?'Weather checkpoint · Revised plan ready. Review, Accept, then Animate.':'Weather checkpoint · Replan failed; remain paused.')+warning);
  }
  function remainingFlight(route,drone){
    const path=route.trajectory||[],cumulative=drone.cumulativeDistances;
    const copyPath=points=>points.map(point=>point.slice());
    // Playback distances include stationary loiter and solver-scaled legs.
    // Rebuilding a haversine distance here can put the assessor ahead of the
    // drone. Without a usable frame, retaining the whole path is conservative.
    if(path.length<2||!Array.isArray(cumulative)||cumulative.length!==path.length||cumulative[0]!==0||
       !Number.isFinite(drone.distanceTraveled)||
       cumulative.some((distance,i)=>!Number.isFinite(distance)||(i&&distance<cumulative[i-1]))){
      return {path:copyPath(path)};
    }
    const distance=Math.max(cumulative[0],Math.min(drone.distanceTraveled,cumulative.at(-1)));
    const split=split_polyline_at_distance(path,distance,cumulative);
    const position=split.splitPoint,index=split.splitIndex+1;
    const result={path:copyPath(split.suffixPoints)};

    // Only route-occurrence indices identify required stops reliably: waypoint
    // IDs may repeat at refuels or revisits. Never invent a shortcut across a
    // target/airport when legacy or stale waypoint metadata is incomplete.
    const sequence=route.route,metadata=route.waypoint_indices;
    if(!Array.isArray(sequence)||sequence.length<2||!metadata)return result;
    const anchors=sequence.map((_,i)=>metadata[i]);
    if(anchors.some((anchor,i)=>!Number.isInteger(anchor)||anchor<0||anchor>=path.length||
        (i&&anchor<=anchors[i-1]))||anchors[0]!==0||anchors.at(-1)!==path.length-1)return result;
    const legs=[];
    let start=index,from=position;
    for(const anchor of anchors){
      if(cumulative[anchor]<=distance)continue;
      legs.push([from.slice(),...copyPath(path.slice(start,anchor+1))]);
      from=path[anchor];start=anchor+1;
    }
    result.legs=legs;
    return result;
  }
  function remaining(){
    const routes={},legs={};
    Object.entries(state.animation.drones||{}).forEach(([did,drone])=>{
      if(!drone.animating||drone.distanceTraveled>=drone.totalDistance)return;
      const flight=remainingFlight(state.routes[did]||{},drone);
      routes[did]=flight.path;
      if(flight.legs)legs[did]=flight.legs;
    });
    return {routes,legs};
  }
  function missedTargets(){
    const visited=new Set((state.visitedTargets||[]).map(String));
    const scheduled=new Set(Object.values(state.routes||{}).flatMap(r=>r.route||[]).map(String));
    return (state.env.targets||[]).filter(t=>!visited.has(String(t.id))&&!scheduled.has(String(t.id)));
  }
  async function assess(data,flight,missed,previous){
    const response=await fetch('/api/weather/impact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      env:state.env,remaining_routes:flight.routes,remaining_legs:flight.legs,missed_targets:missed,previous,current:data.cells,
      start:config.epoch()/1000,end:config.end()/1000,retrieved_at:data.retrievedAt
    })});
    if(!response.ok)throw Error('Weather impact could not be verified.');
    return response.json();
  }
  async function refreshPending(token=generation){
    if(token!==generation)return false;
    if(!pending)return true;
    const request=pending;
    if(config.epoch()>=config.end()){
      status('Paused for weather: mission time is beyond the selected weather window. Extend End and Get forecast before replanning.');return false;
    }
    await config.refreshAt(request.epoch);
    if(token!==generation||pending!==request)return false;
    const data=config.data();
    if(!data||!config.snapshot()||config.snapshot().dirty)return false;
    const impact=await assess(data,request.flight,request.missed,request.previous);
    if(token!==generation||pending!==request)return false;
    if(!impact.canReplan){
      status('Paused for weather: '+impact.reasons.join(' ')+' No revised plan was generated. Expiry is not confirmed clearance. Retry when NOAA supplies weather for this mission time.');return false;
    }
    pending.review={epoch:pending.epoch,reasons:impact.reasons,notices:impact.notices||[],requiresWeatherReview:!!impact.requiresWeatherReview,recoveredTargets:impact.recoveredTargets||[],releasedDetours:impact.releasedDetours||[]};
    readyReview=pending.review;
    pending=null;return true;
  }
  async function checkpoint(reasons,canReplan,impact={},token=generation){
    if(token!==generation||!state.animation.active)return;
    const epoch=config.epoch();
    const flight=remaining(),missed=missedTargets();
    const review={epoch,reasons,notices:impact.notices||[],requiresWeatherReview:!!impact.requiresWeatherReview,recoveredTargets:impact.recoveredTargets||[],releasedDetours:impact.releasedDetours||[]};
    pending={epoch,flight,missed,previous:JSON.parse(JSON.stringify(baseline)),review};
    pauseAnimation();
    status('Weather checkpoint · '+reasons.join(' '));
    appendDebugLine('🌦️ Weather checkpoint: '+reasons.join(' '));
    await prepareCutFromCurrentState();
    if(token!==generation)return;
    drawEnvironment();
    if(!canReplan){
      if(await refreshPending(token)){
        const solved=await config.replan();
        if(token===generation)draftReady(solved,readyReview||review);
      }
      return;
    }
    pending=null;
    const solved=await config.refreshAndReplan(epoch);
    if(token===generation)draftReady(solved,review);
    // Deliberately do not accept or resume automatically.
  }
  async function inspect(force=false){
    const token=generation;
    if(pending){
      if(!busy&&Date.now()-lastPoll>=60000){
        busy=true;lastPoll=Date.now();
        try{
          if(await refreshPending(token)){
            const result=await config.replan();
            if(token===generation)draftReady(result,readyReview||{});
          }
        }catch(error){if(token===generation)status('Paused for weather: '+error.message);}
        finally{if(token===generation)busy=false;}
      }
      return;
    }
    if(!config||busy||!armed||!config.snapshot()||!state.animation.active||
       !document.getElementById('wp-auto-checkpoint').checked)return;
    const now=config.epoch(), end=config.end();
    const boundary=baseline.filter(c=>c.rule==='No fly').map(c=>{
      if(c.validAt)return now>=Date.parse(c.validAt)?c.validAt:'';
      return now>=Date.parse(c.validTo)?c.validTo:'';
    }).join('|');
    const poll=Date.now()-lastPoll>=60000;
    if(!force&&!transitionDue&&!poll&&boundary===lastBoundary&&now<end)return;
    busy=true;
    try{
      lastBoundary=boundary;lastPoll=Date.now();
      if(now>=end){await checkpoint(['Mission has reached the end of the loaded weather window.'],false,{},token);return;}
      let data;
      if(window.weatherDataSource?.current){
        data=await window.weatherDataSource.current();
      }else{
        const response=await fetch('/api/weather/current',{cache:'no-store'});
        data=await response.json();
        if(!response.ok)throw Error(data.detail||'NOAA refresh failed.');
      }
      if(token!==generation||!state.animation.active)return;
      const flight=remaining();
      const impact=await assess(data,flight,missedTargets(),baseline);
      if(token!==generation||!state.animation.active)return;
      if(impact.affected)await checkpoint(impact.reasons,impact.canReplan,impact,token);
      else if(impact.notices?.length)status('Continuing the conservative route. '+impact.notices.join(' '));
    }catch(error){if(token===generation)await checkpoint(['Weather refresh unavailable: '+error.message],false,{},token);}
    finally{if(token===generation){busy=false;transitionDue=false;}}
  }
  function limitStep(seconds){
    if(!config||!armed||!config.snapshot()||!document.getElementById('wp-auto-checkpoint').checked)return seconds;
    if(busy||transitionDue)return 0;
    const now=config.epoch();
    const boundaries=[config.end(),...baseline.flatMap(c=>c.rule==='No fly'?
      [c.validAt,c.validFrom,c.validTo].filter(Boolean).map(Date.parse):[])];
    const next=Math.min(...boundaries.filter(t=>t>now+0.001));
    if(Number.isFinite(next)&&now+seconds*1000>=next){
      transitionDue=true;return Math.max(0,(next-now)/1000);
    }
    return seconds;
  }
  setInterval(inspect,500);
  return {
    configure:value=>{config=value;},
    arm:cells=>{transitionDue=false;baseline=JSON.parse(JSON.stringify(cells));armed=true;lastPoll=Date.now();lastBoundary=baseline.filter(c=>c.rule==='No fly').map(c=>c.validAt?(config.epoch()>=Date.parse(c.validAt)?c.validAt:''):(config.epoch()>=Date.parse(c.validTo)?c.validTo:'')).join('|');},
    reset:()=>{generation++;baseline=[];busy=false;armed=false;lastBoundary='';lastPoll=0;pending=null;transitionDue=false;readyReview=null;},
    limitStep,
    allowSolve:async()=>{const token=generation;try{return await refreshPending(token);}catch(error){if(token===generation)status('Paused for weather: '+error.message);return false;}},
    inspect
  };
})();
