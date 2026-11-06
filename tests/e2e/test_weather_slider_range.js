const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('webapp/weather_preview.js','utf8');
const block=source.slice(source.indexOf('  function updateTimelineWindow(){'),source.indexOf("  get('wp-use-eta').onclick"));
for(const flightHours of [0,13,50]){
 const elements={},start=Date.parse('2026-10-12T02:00:00Z');
 const context={snapshot:{times:[start,start+4*3600000]},missionEnd:start+4*3600000,flightEnd:flightHours?start+flightHours*3600000:null,zone:'UTC',Intl,Date,dualTime:t=>String(t),get:id=>elements[id]||(elements[id]={})};
 vm.createContext(context);vm.runInContext(block+'\nupdateTimelineWindow();',context);
 const hours=elements['wp-time'].max/3600;
 assert(hours>=24);
 if(flightHours)assert(hours>=flightHours+12);
 assert(context.snapshot.times[1]===context.missionEnd,'Browsing range must not silently extend the solve window');
}
console.log('Weather range reserves 24 hours or flight ETA plus 12 hours.');
