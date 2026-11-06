/* Weather page extension: starts only when weatherPreview is explicitly enabled. */
window.PageCapabilities.register('weatherPreview', () => {
  const get=id=>document.getElementById(id);
  let data, snapshot=null, mapLayers=null, mapObject=null, viewer=null, selectedId=null, lastMapBounds=null, followEpoch=null, retrieving=false, weatherFailed=false, missionClockStart=null, planningStart=null, areaTouched=false, flightEnd=null;
  const copy=value=>JSON.parse(JSON.stringify(value));
  let sourceMode='live', recordedCapture=null, originalDemo=null, missionBaseline=null, runBaseline=null, retrievalGeneration=0;
  const missionCoords=()=>{
    // Fit the same environment the canvas displays, including staged live edits.
    const env=getEditableEnv()||state.env;
    return [...(env?.airports||[]),...(env?.targets||[])].filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lng)).map(p=>[p.lat,p.lng]);
  };
  let zone='UTC',missionStart=Math.ceil(Date.now()/60000)*60000,missionEnd=missionStart+4*3600000;
  const partsFormat=z=>new Intl.DateTimeFormat('en-CA',{timeZone:z,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  function wallTime(epoch,z){const p=Object.fromEntries(partsFormat(z).formatToParts(new Date(epoch)).map(p=>[p.type,p.value]));return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;}
  function parseWall(value,z){if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw Error('Enter a complete date and time.');const naive=Date.parse(value+'Z');if(!Number.isFinite(naive))throw Error('Enter a valid date.');const matches=[];const formatter=partsFormat(z);for(let offset=-14*60;offset<=14*60;offset+=15){const epoch=naive+offset*60000;const p=Object.fromEntries(formatter.formatToParts(new Date(epoch)).map(p=>[p.type,p.value]));if(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`===value)matches.push(epoch);}if(!matches.length)throw Error('That local time does not exist during the daylight-saving change. Choose another time.');if(matches.length>1)throw Error('That local time occurs twice during the daylight-saving change. Switch to UTC to specify it.');return matches[0];}
  function localLabel(epoch){return new Intl.DateTimeFormat('en-US',{timeZone:zone,month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}).format(new Date(epoch));}
  function dualTime(epoch){const utcLabel=new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',month:'short',day:'numeric',year:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(epoch))+' UTC';return zone==='UTC'?utcLabel:localLabel(epoch)+' · '+utcLabel;}
  function updateWindow(mark=true){try{const a=parseWall(get('wp-start').value,zone),b=parseWall(get('wp-end').value,zone);if(b<=a)throw Error('End must be after start.');const didChange=a!==missionStart||b!==missionEnd;missionStart=a;missionEnd=b;for(const [id,t] of [['start',a],['end',b]]){get('wp-'+id+'-display').textContent=new Intl.DateTimeFormat('en-GB',{timeZone:zone,month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(t)).replace(',', '');get('wp-'+id+'-display').title=new Intl.DateTimeFormat('en-GB',{timeZone:zone,dateStyle:'full',timeStyle:'short',hourCycle:'h23'}).format(new Date(t));}get('wp-window-error').hidden=true;get('wp-get').disabled=false;if(mark&&didChange)changed();return true;}catch(e){get('wp-window-error').textContent=e.message;get('wp-window-error').hidden=false;get('wp-get').disabled=true;return false;}}

  get('wp-start').value=wallTime(missionStart,zone);get('wp-end').value=wallTime(missionEnd,zone);
  function updateTimelineWindow(){
    if(!snapshot)return;
    const end=Math.max(snapshot.times[0]+24*3600000,snapshot.times.at(-1)+12*3600000,(flightEnd||0)+12*3600000);
    get('wp-time').max=(end-snapshot.times[0])/1000;
    get('wp-time-start').textContent=new Intl.DateTimeFormat('en-GB',{timeZone:zone,month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(snapshot.times[0]));
    get('wp-time-end').textContent=new Intl.DateTimeFormat('en-GB',{timeZone:zone,month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(end));
    const short=flightEnd&&flightEnd>missionEnd;
    get('wp-flight-window').hidden=!short;
    if(short)get('wp-flight-warning').textContent='Flight ETA '+dualTime(flightEnd)+'. Weather window ends earlier.';
  }
  get('wp-use-eta').onclick=async()=>{
    if(!flightEnd)return;
    missionEnd=Math.ceil(flightEnd/60000)*60000;
    get('wp-end').value=wallTime(missionEnd,zone);updateWindow();changed();
    await get('wp-get').onclick();updateTimelineWindow();if(snapshot&&!snapshot.dirty&&!weatherFailed)get('wp-status').textContent='Weather window updated. Run Planner again before accepting.';
  };
  const fmt=t=>dualTime(typeof t==='string'?Date.parse(t):t);
  // Page-lifetime subscription; viewer recreation keeps the same overlay consumer.
  cesiumView.subscribeLifecycle({onInit:value=>{viewer=value;},onRender:render3d});
  const active=(c,t)=>c.validAt?Date.parse(c.validAt)===t:Date.parse(c.validFrom)<=t&&t<Date.parse(c.validTo);
  function time(){return snapshot ? (get('wp-follow').checked && followEpoch!==null ? followEpoch : snapshot.times[0]+Number(get('wp-time').value)*1000) : undefined;}
  function cells(){return snapshot&&time()>=snapshot.times[0]?snapshot.cells.filter(c=>active(c,time())&&(c.rule==='No fly'?get('wp-no-fly').checked:get('wp-other').checked)):[];}
  function render3d(){if(!viewer||viewer.isDestroyed())return;const old=viewer.entities.values.filter(e=>e.id.startsWith('weather-preview-'));old.forEach(e=>viewer.entities.remove(e));if(!snapshot)return;cells().forEach(c=>{const polys=c.geometry.type==='Polygon'?[c.geometry.coordinates]:c.geometry.coordinates;polys.forEach((rings,i)=>{const coords=rings[0].flatMap(p=>[p[0],p[1]]);const color=Cesium.Color.fromCssColorString(c.rule==='No fly'?'#ef4444':'#22d3ee');const props=c.properties;const hasBounds=c.product==='SIGMET'&&Number.isFinite(props.base)&&Number.isFinite(props.top);viewer.entities.add({id:'weather-preview-'+c.id+'-'+i,name:c.product+' '+c.identity,polygon:{hierarchy:new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(coords),rings.slice(1).map(r=>new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(r.flatMap(p=>[p[0],p[1]]))))),height:hasBounds?props.base*.3048:0,extrudedHeight:hasBounds?props.top*.3048:undefined,material:color.withAlpha(c.id===selectedId ? .15 : .025),outline:true,outlineWidth:c.rule==='No fly'?2:1,outlineColor:color.withAlpha(c.id===selectedId?1:(c.rule==='No fly'?.85:.35))}});});});}
  function draw(){if(mapState.leafletMap){if(mapLayers)mapLayers.remove();mapLayers=L.layerGroup().addTo(mapState.leafletMap);if(snapshot){L.rectangle(snapshot.bounds,{color:'#94a3b8',weight:1,dashArray:'5 5',fill:false,interactive:false}).addTo(mapLayers);for(const c of cells()){L.geoJSON(c.geometry,{style:{color:c.rule==='No fly'?'#ef4444':'#22d3ee',weight:c.id===selectedId?3:(c.rule==='No fly'?2:1),opacity:c.id===selectedId?1:(c.rule==='No fly'?.85:.45),fillOpacity:c.id===selectedId ? .10 : .018},onEachFeature:(feature,layer)=>layer.on('click',()=>details(c))}).addTo(mapLayers);}}}render3d();if(snapshot){get('wp-at').textContent=fmt(time());const published=data.forecastTimes.some(t=>Date.parse(t)===time());get('wp-coverage').textContent=published?'US G-AIRMET slice + international SIGMETs · Partial coverage':'International SIGMETs within validity · G-AIRMET coverage is US only';if(time()<snapshot.times[0]||!snapshot.cells.some(c=>active(c,time())))get('wp-coverage').textContent='Weather unavailable at this time · Coverage gaps do not mean clear weather';const list=get('wp-cells');list.replaceChildren();const shown=cells();shown.slice(0,5).forEach(c=>{const b=document.createElement('button');b.className='small-btn';b.style.textAlign='left';b.textContent=c.product+' '+c.identity+' · '+c.name+' · '+c.rule;b.onclick=()=>details(c);list.append(b);});if(shown.length>5){const b=document.createElement('button');b.className='small-btn';b.textContent='Show '+(shown.length-5)+' more';b.onclick=()=>{b.remove();shown.slice(5).forEach(c=>{const row=document.createElement('button');row.className='small-btn';row.textContent=c.product+' '+c.identity+' · '+c.name;row.onclick=()=>details(c);list.append(row);});};list.append(b);}if(!shown.length)list.textContent='No visible advisories at this time. Coverage is incomplete; this does not mean clear weather.';}}
  function details(c){selectedId=c.id;get('wp-more').open=true;draw();const box=get('wp-detail');box.replaceChildren();for(const text of [c.product+' '+c.identity+' · '+c.name,c.rule+' (planner policy)',c.band,c.validAt?'Forecast valid at '+fmt(c.validAt):'Valid '+fmt(c.validFrom)+' — '+fmt(c.validTo),'3D: unnormalized or missing vertical bounds are displayed as a ground footprint, not an invented volume.']){const p=document.createElement('p');p.style.margin='7px 0';p.textContent=text;box.append(p);}const d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('p');s.textContent='Source advisory';p.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';p.textContent=c.raw;d.append(s,p);box.append(d);}
  function changed(){if(snapshot){snapshot.dirty=true;get('wp-status').textContent='View or mission window changed · Previous snapshot retained';get('wp-get').textContent='↻ Update forecast';}}
  get('zoom-slider').addEventListener('input',()=>{areaTouched=true;changed();});get('btn-reset-view').addEventListener('click',()=>{areaTouched=true;changed();});
  const originalSyncMap=syncViewStateFromLeaflet;syncViewStateFromLeaflet=(...args)=>{const result=originalSyncMap(...args);if(mapState.leafletMap)lastMapBounds=mapState.leafletMap.getBounds();if(snapshot&&areaTouched&&mapState.leafletMap){const b=mapState.leafletMap.getBounds(),old=snapshot.bounds;if(Math.abs(b.getSouth()-old[0][0])>1e-6||Math.abs(b.getWest()-old[0][1])>1e-6||Math.abs(b.getNorth()-old[1][0])>1e-6||Math.abs(b.getEast()-old[1][1])>1e-6)changed();}return result;};
  function connectMap(){const map=mapState.leafletMap;if(!map||map===mapObject)return;mapObject=map;lastMapBounds=map.getBounds();map.on('dragstart',()=>{areaTouched=true;});map.getContainer().addEventListener('wheel',()=>{areaTouched=true;},{passive:true});map.on('moveend',()=>{get('wp-area').textContent='Current Map view';syncViewStateFromLeaflet();});draw();}
  get('wp-fit').onclick=()=>{
    const coords=missionCoords();
    if(!coords.length){get('wp-status').textContent='Fit mission needs airports or targets with latitude/longitude.';return;}
    areaTouched=true;
    toggle3dMode(false);toggleMapMode(true);connectMap();
    const map=mapState.leafletMap;
    map.invalidateSize({pan:false});
    map.options.zoomSnap=.1;
    // Commit one settled view for the tile map, canvas and weather area.
    map.fitBounds(coords,{padding:[45,45],animate:false});
    syncViewStateFromLeaflet();updateZoomSlider();draw();
  };
  function sourceLabel(){return sourceMode==='recorded'?'Recorded NOAA':'Live NOAA';}
  function renderSource(){
    if(get('wp-data-source'))get('wp-data-source').value=sourceMode;
    if(get('wp-source-mode'))get('wp-source-mode').textContent=sourceMode==='recorded'?'Recorded demo · original weather dates':'Live NOAA · latest available weather';
    if(get('wp-save-demo'))get('wp-save-demo').disabled=!runBaseline;
  }
  window.weatherDataSource={
    getMode:()=>sourceMode,
    async current(){
      if(sourceMode==='recorded'){
        if(!recordedCapture)throw Error('Load a recorded demo first.');
        return copy(recordedCapture);
      }
      const response=await fetch('/api/weather/current',{cache:'no-store'});
      const result=await response.json();
      if(!response.ok)throw Error(result.detail||'NOAA retrieval failed.');
      return normalizeWeather(result);
    }
  };
  function cleanMission(){
    const env=copy(state.env||{}),configs=copy(state.droneConfigs||{});
    delete env.weather_snapshot;delete env.checkpoints;
    env.sams=(env.sams||[]).filter(s=>!s.weather_ring);
    for(const cfg of Object.values(configs))delete cfg.start_fuel;
    env.drone_configs=copy(configs);
    const mission={schema:'isr_env_v1',is_segmented:false,env,drone_configs:configs};
    if(missionBaseline?.map_view)mission.map_view=copy(missionBaseline.map_view);
    return mission;
  }
  function installWeather(capture,bounds,a,b){
    data=copy(capture);
    const [[south,west],[north,east]]=bounds;
    const matched=data.cells.filter(c=>c.box[2]>=west&&c.box[0]<=east&&c.box[3]>=south&&c.box[1]<=north);
    const solverCells=matched.filter(c=>c.validAt?Date.parse(c.validAt)>=a&&Date.parse(c.validAt)<=b:Date.parse(c.validTo)>a&&Date.parse(c.validFrom)<b);
    const times=[...new Set([a,b,...data.forecastTimes.map(Date.parse).filter(t=>t>=a&&t<=b),...matched.flatMap(c=>c.validAt?[]:[Date.parse(c.validFrom),Date.parse(c.validTo)]).filter(t=>t>=a&&t<=b)])].sort((a,b)=>a-b);
    snapshot={bounds:copy(bounds),cells:matched,solverCells,times};
    if(missionClockStart===null||(!state.checkpoint?.active&&!state.animation.active&&!state.animation.elapsedMissionSeconds))missionClockStart=a;
    get('wp-time').max=(b-a)/1000;get('wp-time').step=1;followEpoch=null;get('wp-time').value=0;get('wp-timeline').hidden=false;
    get('wp-status').textContent=matched.length+' advisories · '+sourceLabel()+' captured '+fmt(data.retrievedAt)+' · Partial coverage';
    get('wp-get').textContent=sourceMode==='recorded'?'✓ Recorded weather loaded':(matched.length?'✓ Forecast loaded':'✓ Retrieved · No matching advisories');
    get('wp-source').textContent=sourceLabel()+' captured '+fmt(data.retrievedAt)+'. G-AIRMET slices: '+data.forecastTimes.map(fmt).join('; ')+'. International SIGMETs only within validity. Partial coverage; no advisories does not mean clear weather.';
    selectedId=null;get('wp-more').open=false;get('wp-detail').replaceChildren();updateTimelineWindow();draw();renderSource();
  }
  get('wp-get').onclick=async ()=>{
    if(retrieving)return;
    if(!lastMapBounds&&!mapState.leafletMap){get('wp-status').textContent='Select an area in Map view first.';return;}
    if(!updateWindow())return;
    const a=planningStart??missionStart,b=missionEnd;
    if(!Number.isFinite(a)||!Number.isFinite(b)||b<=a){get('wp-status').textContent='End must be after start.';return;}
    const bounds=(!cesiumView.isActive()&&mapState.leafletMap)?mapState.leafletMap.getBounds():lastMapBounds;
    const area=[[bounds.getSouth(),bounds.getWest()],[bounds.getNorth(),bounds.getEast()]];
    get('wp-area').textContent=cesiumView.isActive()?'Last selected Map area':'Current Map view';areaTouched=false;
    const fetchStart=missionStart,fetchEnd=missionEnd,fetchEnv=state.env,generation=++retrievalGeneration;
    retrieving=true;weatherFailed=true;if(snapshot)snapshot.dirty=true;
    get('wp-get').disabled=true;get('wp-get').textContent=sourceMode==='recorded'?'Loading recorded weather…':'Getting NOAA data…';
    get('wp-status').textContent=sourceMode==='recorded'?'Applying recorded NOAA weather…':'Retrieving current NOAA SIGMETs and G-AIRMETs…';
    try{
      const result=await window.weatherDataSource.current();
      if(generation!==retrievalGeneration)return;
      if(state.env!==fetchEnv||missionStart!==fetchStart||missionEnd!==fetchEnd)throw Error('Mission times changed during retrieval. Get forecast again.');
      retrieving=false;weatherFailed=false;get('wp-get').disabled=false;
      installWeather(result,area,a,b);
      if(!state.checkpoint?.active&&!state.animation.elapsedMissionSeconds&&!missionState.committedSegments?.length){
        missionBaseline=cleanMission();
        runBaseline={mission:copy(missionBaseline),weather:copy(result),window:{start:new Date(a).toISOString(),end:new Date(b).toISOString(),timezone:zone},bounds:copy(area)};
      }
      renderSource();syncViewStateFromLeaflet();
    }catch(error){
      if(generation!==retrievalGeneration)return;
      retrieving=false;get('wp-status').textContent=error.message;get('wp-get').textContent='Retry forecast';get('wp-get').disabled=false;
    }
  };
  get('wp-time').oninput=()=>{get('wp-follow').checked=false;followEpoch=null;selectedId=null;get('wp-detail').replaceChildren();draw();};['wp-no-fly','wp-other'].forEach(id=>get(id).onchange=()=>{selectedId=null;get('wp-detail').replaceChildren();draw();});get('wp-start').oninput=()=>updateWindow();get('wp-end').oninput=()=>updateWindow();get('wp-zone').onchange=()=>{zone=get('wp-zone').value;get('wp-start').value=wallTime(missionStart,zone);get('wp-end').value=wallTime(missionEnd,zone);updateWindow(false);if(snapshot){for(const [id,t] of [['wp-time-start',snapshot.times[0]],['wp-time-end',snapshot.times.at(-1)]])get(id).textContent=new Intl.DateTimeFormat('en-GB',{timeZone:zone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(t));updateTimelineWindow();draw();}};updateWindow(false);document.querySelectorAll('[data-picker]').forEach(button=>{const open=()=>{const field=get(button.dataset.picker);if(typeof field.showPicker==='function'){try{field.showPicker();}catch{field.focus();}}else field.focus();};button.onclick=open;get(button.dataset.picker+'-display').onclick=open;});
  ['btn-view-map','btn-view-grid','btn-view-3d'].forEach(id=>get(id).addEventListener('click',()=>setTimeout(()=>{connectMap();draw();},300)));

  function validEpoch(value){return typeof value==='string'&&Number.isFinite(Date.parse(value));}
  function normalizeWeather(weather){
    if(!weather||!validEpoch(weather.retrievedAt)||!Array.isArray(weather.cells)||!Array.isArray(weather.forecastTimes)||!weather.forecastTimes.every(validEpoch))throw Error('Weather has invalid capture or forecast times.');
    const normalized=weather.cells.map(cell=>{
      const label='Weather advisory '+(cell?.identity||cell?.id||'(unknown)');
      if(!cell||!Array.isArray(cell.box)||cell.box.length!==4||!cell.box.every(Number.isFinite)||!cell.geometry||!['Polygon','MultiPolygon'].includes(cell.geometry.type))throw Error(label+' has an invalid area.');
      const polygons=cell.geometry.type==='Polygon'?[cell.geometry.coordinates]:cell.geometry.coordinates;
      const invalid=()=>{throw Error(label+' has invalid polygon coordinates.');};
      if(!Array.isArray(polygons)||!polygons.length)invalid();
      const closed=polygons.map(p=>{
        if(!Array.isArray(p)||!p.length)invalid();
        return p.map(r=>{
          if(!Array.isArray(r)||r.length<3||!r.every(v=>Array.isArray(v)&&v.length>=2&&Number.isFinite(v[0])&&Number.isFinite(v[1])&&Math.abs(v[1])<=90&&Math.abs(v[0])<=540)||new Set(r.map(v=>`${v[0]},${v[1]}`)).size<3)invalid();
          // NOAA sometimes omits the repeated closing position. Preserve the
          // boundary and all advisories; add only that closing position. Older
          // recorded captures need the same normalization as the live adapter.
          const first=r[0],last=r.at(-1);
          return first[0]===last[0]&&first[1]===last[1]?r:[...r,first];
        });
      });
      if(cell.validAt?!validEpoch(cell.validAt):(!validEpoch(cell.validFrom)||!validEpoch(cell.validTo)||Date.parse(cell.validTo)<=Date.parse(cell.validFrom)))throw Error(label+' has an invalid validity period.');
      if(!cell.id||typeof cell.rule!=='string'||!cell.properties||typeof cell.properties!=='object')throw Error(label+' metadata is incomplete.');
      return {...cell,geometry:{...cell.geometry,coordinates:cell.geometry.type==='Polygon'?closed[0]:closed}};
    });
    return copy({...weather,cells:normalized});
  }
  function demoPlaybackSettings(speed){
    const speeds=[.25,.5,1,2,4,8,16,32,64,128];
    if(!Number.isFinite(speed))return null;
    for(const factor of [1,10,100]){
      const index=speeds.indexOf(speed/factor);
      if(index!==-1)return {factor,index};
    }
    return null;
  }
  function validateDemo(input){
    const demo=copy(input);
    if(demo?.schema!=='isr_weather_demo_v1'||!demo.name||demo.mission?.schema!=='isr_env_v1'||demo.mission.is_segmented!==false)throw Error('Select an ISR weather demo JSON file.');
    const env=demo.mission.env,configs=demo.mission.drone_configs||env?.drone_configs;
    if(!env||!Array.isArray(env.airports)||!env.airports.length||!Array.isArray(env.targets)||!Array.isArray(env.sams)||!configs||typeof configs!=='object')throw Error('Demo mission is incomplete.');
    if((env.checkpoints&&Object.keys(env.checkpoints).length)||env.weather_snapshot||env.sams.some(s=>s.weather_ring)||demo.mission.segments)throw Error('Demo mission must contain the original environment, without checkpoint routes or injected weather.');
    const points=[...env.airports,...env.targets],ids=new Set();
    for(const p of points){if(!p.id||ids.has(p.id)||!Number.isFinite(p.lat)||!Number.isFinite(p.lng)||Math.abs(p.lat)>90||Math.abs(p.lng)>180)throw Error('Demo mission has invalid or duplicate airports/targets.');ids.add(p.id);}
    const airportIds=new Set(env.airports.map(p=>p.id));
    for(const cfg of Object.values(configs))if(!cfg||typeof cfg!=='object'||(cfg.enabled!==false&&(!airportIds.has(cfg.start_airport)||!airportIds.has(cfg.end_airport)||!Number.isFinite(cfg.speed)||cfg.speed<=0||!Number.isFinite(cfg.fuel_budget)||cfg.fuel_budget<=0)))throw Error('Demo aircraft settings are invalid.');
    const w=demo.window,b=demo.bounds;
    if(!w||!validEpoch(w.start)||!validEpoch(w.end)||Date.parse(w.end)<=Date.parse(w.start)||Date.parse(w.start)%60000||Date.parse(w.end)%60000)throw Error('Demo mission dates must be valid whole minutes with end after start.');
    if(!['UTC','America/Chicago','America/New_York','America/Denver','America/Los_Angeles'].includes(w.timezone))throw Error('Demo time zone is unsupported.');
    if(!Array.isArray(b)||b.length!==2||!b.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite))||b[0][0]>=b[1][0]||b[0][1]>=b[1][1]||b.some(p=>Math.abs(p[0])>90||Math.abs(p[1])>180))throw Error('Demo map bounds are invalid.');
    const view=demo.mission.map_view;
    if(view&&((view.center&&(!Number.isFinite(view.center.lat)||!Number.isFinite(view.center.lng)||Math.abs(view.center.lat)>90||Math.abs(view.center.lng)>180))||(view.zoom!==undefined&&(!Number.isFinite(view.zoom)||view.zoom<0||view.zoom>24))||(view.bounds&&!['south','west','north','east'].every(k=>Number.isFinite(view.bounds[k])))))throw Error('Demo saved map view is invalid.');
    if(demo.policy?.safety_margin_km!==5)throw Error('This planner supports the 5 km weather safety margin.');
    if(demo.playback?.speed!==undefined&&!demoPlaybackSettings(demo.playback.speed))throw Error('Demo playback speed is unsupported.');
    demo.weather=normalizeWeather(demo.weather);
    return demo;
  }
  function clearWeather(){
    retrievalGeneration++;retrieving=false;data=null;snapshot=null;selectedId=null;followEpoch=null;weatherFailed=false;missionClockStart=null;planningStart=null;flightEnd=null;areaTouched=false;runBaseline=null;
    get('wp-flight-window').hidden=true;window.weatherCheckpoints.reset();get('wp-checkpoint-status').textContent='';
    get('wp-timeline').hidden=true;get('wp-get').disabled=false;get('wp-get').textContent='Get forecast';get('wp-solve-mode').textContent='Planning without weather';
    get('wp-cells').replaceChildren();get('wp-detail').replaceChildren();get('wp-follow').checked=true;get('wp-source').textContent=sourceMode==='recorded'?'Load a demo to use its saved NOAA capture and original dates.':'Get forecast retrieves the latest available NOAA advisories. Coverage gaps do not mean clear weather.';draw();renderSource();
  }
  function setTimes(w){zone=w.timezone;missionStart=Date.parse(w.start);missionEnd=Date.parse(w.end);get('wp-zone').value=zone;get('wp-start').value=wallTime(missionStart,zone);get('wp-end').value=wallTime(missionEnd,zone);updateWindow(false);}
  function nowWindow(){const start=Math.ceil(Date.now()/60000)*60000;return {start:new Date(start).toISOString(),end:new Date(start+16*3600000).toISOString(),timezone:zone};}
  const originalLoadFromJson=loadFromJson;
  function loadDemo(input,filename=''){
    const demo=validateDemo(input); // Complete validation before changing the running mission.
    originalLoadFromJson(copy(demo.mission),filename||demo.name);
    clearWeather();sourceMode='recorded';recordedCapture=copy(demo.weather);originalDemo=copy(demo);missionBaseline=copy(demo.mission);
    setTimes(demo.window);runBaseline={mission:copy(demo.mission),weather:copy(demo.weather),window:copy(demo.window),bounds:copy(demo.bounds)};
    missionClockStart=missionStart;installWeather(demo.weather,demo.bounds,missionStart,missionEnd);
    if(mapState.leafletMap?.fitBounds){areaTouched=false;mapState.leafletMap.fitBounds(demo.bounds,{animate:false});connectMap();}
    const playback=demoPlaybackSettings(demo.playback?.speed??1280),slider=get('mc-speed-slider'),boost=get('mc-speed-boost'),boostFactor=get('mc-speed-boost-factor');
    if(slider){if(boost)boost.checked=playback.factor!==1;if(boostFactor)boostFactor.value=String(playback.factor===1?100:playback.factor);slider.value=String(playback.index);slider.dispatchEvent(new Event('input',{bubbles:true}));}
    get('wp-status').textContent='Recorded demo loaded: '+demo.name+'. Run Planner → Accept → Animate.';
    renderSource();return demo;
  }
  loadFromJson=(input,...args)=>{
    if(input?.schema==='isr_weather_demo_v1')return loadDemo(input,...args);
    const result=originalLoadFromJson(input,...args);
    sourceMode='live';recordedCapture=null;originalDemo=null;clearWeather();setTimes(nowWindow());
    missionBaseline=cleanMission();if(input?.map_view)missionBaseline.map_view=migrateLegacyMapCoordinates({map_view:input.map_view}).map_view;
    get('wp-status').textContent='Mission imported. Frame its area in Map, then get forecast.';
    renderSource();return result;
  };
  function exportDemo(){
    if(!runBaseline)throw Error('Load weather before saving a demo.');
    return {schema:'isr_weather_demo_v1',name:originalDemo?.name||state.missionName||state.envFilename?.replace(/\.json$/i,'')||'Weather mission',description:originalDemo?.description||'Recorded NOAA weather and mission. Re-solve using the original dates.',...copy(runBaseline),policy:{safety_margin_km:5},playback:{speed:Number(state.animation.speedMultiplier)||1280}};
  }
  function resetToStart(){
    if(!runBaseline){clearWeather();return;}
    retrievalGeneration++;retrieving=false;weatherFailed=false;planningStart=null;flightEnd=null;followEpoch=null;lastFollowTime=null;areaTouched=false;
    setTimes(runBaseline.window);missionClockStart=missionStart;get('wp-follow').checked=true;get('wp-flight-window').hidden=true;get('wp-get').disabled=false;
    window.weatherCheckpoints.reset();installWeather(runBaseline.weather,runBaseline.bounds,missionStart,missionEnd);window.weatherCheckpoints.arm(snapshot.solverCells);
    get('wp-checkpoint-status').textContent='';get('wp-status').textContent='Original mission weather restored. Ready to replay from the start.';
  }
  function allowAnimation(){
    const accepted=missionState.committedSegments?.at(-1)?.solution||missionState.seg0FullSolution;
    const used=accepted?.weatherSnapshot||null;
    let reason='';
    if(retrieving)reason='Wait for weather retrieval, then Run Planner → Accept before animating.';
    else if(weatherFailed)reason='Weather retrieval failed. Retry forecast, then Run Planner → Accept before animating.';
    else if(snapshot||used){
      if(!accepted||!used||!snapshot||JSON.stringify(used)!==JSON.stringify(capturedWeatherSnapshot()))reason=accepted?'Loaded weather differs from the accepted route. Reset to replay the original weather, or load the mission/demo again and solve.':'Run Planner → Accept before animating.';
      else{
        try{
          const start=parseWall(get('wp-start').value,zone),end=parseWall(get('wp-end').value,zone);
          if(start!==Math.floor(used.start/60000)*60000||end!==used.end)reason='Mission times changed. Reset to replay the original weather, or load the mission/demo again and solve.';
        }catch(error){reason=error.message;}
      }
    }
    if(!reason)return true;
    get('wp-status').textContent=reason;
    const status=get('mc-status-text');if(status)status.textContent=reason;
    appendDebugLine('⏸️ Animate blocked: '+reason);return false;
  }
  window.weatherPreview={loadDemo,exportDemo,resetToStart,allowAnimation,getMode:()=>sourceMode,
    useRecordedCapture(capture){const normalized=capture?normalizeWeather(capture):null;sourceMode='recorded';recordedCapture=normalized;renderSource();}
  };
  if(get('wp-data-source'))get('wp-data-source').onchange=()=>{
    const mode=get('wp-data-source').value;if(mode===sourceMode)return;
    if(state.animation.active&&typeof stopAnimation==='function')stopAnimation();
    const baseline=runBaseline&&(state.checkpoint?.active||state.animation.elapsedMissionSeconds)?copy(runBaseline.mission):cleanMission();
    originalLoadFromJson(baseline,state.envFilename||'');missionBaseline=copy(baseline);
    sourceMode=mode;clearWeather();
    if(mode==='live'){recordedCapture=null;originalDemo=null;setTimes(nowWindow());get('wp-status').textContent='Live NOAA selected. Get forecast, then Run Planner.';}
    else{get('wp-status').textContent='Choose Load demo to restore its mission and recorded weather.';get('wp-demo-file')?.click();}
    renderSource();
  };
  if(get('wp-load-demo'))get('wp-load-demo').onclick=()=>{get('wp-demo-file').value='';get('wp-demo-file').click();};
  if(get('wp-demo-file'))get('wp-demo-file').onchange=async event=>{
    const file=event.target.files?.[0];if(!file)return;
    try{loadDemo(JSON.parse(await file.text()),file.name);}catch(error){get('wp-status').textContent=error.message;appendDebugLine('Demo load failed: '+error.message);}
  };
  if(get('wp-save-demo'))get('wp-save-demo').onclick=()=>{
    try{const demo=exportDemo();downloadJsonFile(demo,(demo.name.replace(/[^a-z0-9_-]+/gi,'_')||'weather')+'_demo.json');get('wp-status').textContent='Demo saved with its original mission and recorded NOAA weather.';}catch(error){get('wp-status').textContent=error.message;}
  };
  renderSource();
  function ready(){
    if(!state.env){setTimeout(ready,100);return;}
    loadFromJson({airports:[],targets:[],sams:[]},'Import your mission JSON');
    get('wp-status').textContent='Import a mission JSON, frame its area in Map, then get forecast.';
    setEditMode(false);toggleMapMode(true);connectMap();draw();
  }
  function capturedWeatherSnapshot(){
    if(!snapshot)return null;
    return {retrievedAt:data.retrievedAt,start:snapshot.times[0],end:snapshot.times.at(-1),bounds:snapshot.bounds,safety_margin_km:5,policy:'mission-window union',advisories:snapshot.solverCells};
  }
  const originalBuildSolveRequest=buildSolveRequest;
  buildSolveRequest=(...args)=>{
    const request=originalBuildSolveRequest(...args);
    request.weatherApplied=!!snapshot;
    if(snapshot){
      const env=JSON.parse(JSON.stringify(request.payload.env));
      const obstacles=snapshot.solverCells.filter(c=>c.rule==='No fly').flatMap(c=>{
        const polygons=c.geometry.type==='Polygon'?[c.geometry.coordinates]:c.geometry.coordinates;
        return polygons.map((rings,i)=>{const ring=rings[0];return {id:'WX-'+c.id+'-'+i,center:{lat:ring[0][1],lng:ring[0][0]},range_km:0,range:0,weather_ring:ring.map(p=>p.slice(0,2))};});
      });
      env.sams=[...(env.sams||[]).filter(s=>!s.weather_ring),...obstacles];
      env.weather_snapshot=JSON.parse(JSON.stringify(capturedWeatherSnapshot()));
      request.payload.env=env;request.envToSolve=env;
      request.payload.use_sam_aware_distances=true;
    }
    return request;
  };
  const originalAcceptSolution=acceptSolution;
  acceptSolution=(...args)=>{
    const draft=missionState.draftSolution;
    let reason='';
    if(draft&&(snapshot||draft.weatherSnapshot)){
      const used=draft.weatherSnapshot;
      if(retrieving)reason='Wait for weather retrieval before accepting.';
      else if(weatherFailed)reason='Weather retrieval failed. Retry forecast and Run Planner before accepting.';
      else if(!used)reason='This draft was solved without the loaded weather. Run Planner again before accepting.';
      else if(!snapshot)reason='The weather used by this draft is no longer loaded. Get forecast and Run Planner again.';
      else {
        try{
          const selectedStart=parseWall(get('wp-start').value,zone),selectedEnd=parseWall(get('wp-end').value,zone);
          if(selectedStart!==Math.floor(used.start/60000)*60000||selectedEnd!==used.end){
            reason='Mission times changed after this solve. Update forecast and Run Planner again before accepting.';
          } else if(JSON.stringify(used)!==JSON.stringify(capturedWeatherSnapshot())){
            reason='The loaded forecast changed after this solve. Run Planner again before accepting.';
          }
        }catch(error){reason=error.message;}
      }
    }
    if(reason){
      get('wp-status').textContent=reason;
      const missionStatus=get('mc-status-text');if(missionStatus)missionStatus.textContent=reason;
      appendDebugLine('⏸️ Accept blocked: '+reason);return;
    }
    return originalAcceptSolution(...args);
  };
  function reportWeatherSolveBlock(message){
    get('wp-status').textContent=message;
    get('wp-solve-mode').textContent='Planner has not started';
    const missionStatus=get('mc-status-text');
    if(missionStatus)missionStatus.textContent=message;
    appendDebugLine('⏸️ Solve blocked: '+message);
  }
  const originalRunPlanner=runPlanner;
  runPlanner=async (...args)=>{
    if(!await window.weatherCheckpoints.allowSolve()){reportWeatherSolveBlock(get('wp-checkpoint-status').textContent);return;}
    if(retrieving||weatherFailed){reportWeatherSolveBlock(retrieving?'Wait for NOAA retrieval before planning.':'NOAA retrieval failed. Retry forecast before planning.');return;}
    if(snapshot?.dirty){reportWeatherSolveBlock('Map area or mission times changed. Click Update forecast, then Run Planner.');return;}
    get('wp-solve-mode').textContent=snapshot?'Solving with weather avoidance · Partial coverage':'Solving without weather';
    const result=await originalRunPlanner(...args);
    if(result?.success&&snapshot){
      const durations=Object.entries(result.routes||{}).filter(([did])=>state.droneConfigs[did]?.enabled!==false).map(([did,r])=>{
        const speed=Number(state.droneConfigs[did]?.speed)||1;
        const distance=Number(r.anim_frame?.total_distance??r.distance)||0;
        return 60*((Number(r.launch_delay)||0)+distance/speed)+2;
      });
      flightEnd=(missionClockStart??missionStart)+(state.animation.elapsedMissionSeconds||0)*1000+Math.max(0,...durations)*1000;
      updateTimelineWindow();window.weatherCheckpoints.arm(snapshot.solverCells);
    }
    if(result?.success===false&&result.errors?.length)get('wp-status').textContent=result.errors.join(' ');get('wp-solve-mode').textContent=result?.success?(snapshot?'Solved with weather avoidance · 5 km margin · Partial coverage':'Solved without weather'):'No new solution accepted for review';
    return result;
  };
  let lastFollowTime=null;
  function followMission(){
    if(!snapshot||!get('wp-follow').checked)return;
    const epoch=(missionClockStart??missionStart)+Math.floor(state.animation.elapsedMissionSeconds||0)*1000;
    if(epoch===lastFollowTime&&followEpoch!==null)return;
    lastFollowTime=epoch;followEpoch=epoch;
    get('wp-time').value=Math.max(0,Math.min(Number(get('wp-time').max),(epoch-snapshot.times[0])/1000));
    selectedId=null;get('wp-detail').replaceChildren();draw();
  }
  get('wp-follow').onchange=()=>{lastFollowTime=null;if(get('wp-follow').checked)followMission();else {followEpoch=null;draw();}};
  window.weatherCheckpoints.configure({
    snapshot:()=>snapshot,
    epoch:()=>(missionClockStart??missionStart)+(state.animation.elapsedMissionSeconds||0)*1000,
    end:()=>missionEnd,
    data:()=>data,
    recordReview:review=>{if(missionState.draftSolution)missionState.draftSolution.weatherReview=JSON.parse(JSON.stringify(review));},
    replan:async()=>!!(await runPlanner())?.success,
    async refreshAt(epoch){
      planningStart=epoch;
      missionStart=Math.floor(epoch/60000)*60000;
      get('wp-start').value=wallTime(missionStart,zone);updateWindow(false);
      await get('wp-get').onclick();
    },
    async refreshAndReplan(epoch){
      planningStart=epoch;
      missionStart=Math.floor(epoch/60000)*60000;
      get('wp-start').value=wallTime(missionStart,zone);updateWindow(false);
      const generation=retrievalGeneration+1;
      await get('wp-get').onclick();
      if(generation!==retrievalGeneration||!snapshot||snapshot.dirty||weatherFailed)return false;
      const result=await runPlanner();return !!result?.success;
    }
  });
  setInterval(followMission,200);
  window.addEventListener('load',ready,{once:true});
});
