'use strict';
const n=require('../../core/network');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const p=d.memory_profile;n.requireValue(p.format_version===1&&p.complete===true,'Unknown/incomplete memory profile');
 n.text(p.message_type,'message type');n.text(p.operation,'processing operation');n.digest(p.payload_digest);
 n.number(p.payload_bytes,'payload bytes');n.number(p.peak_bytes,'peak resident/allocation');n.number(p.limit_bytes,'workload memory limit',1);
 n.requireValue(p.units==='bytes'&&p.capture_kind==='allocation-timeline','Attributable allocation timeline required');
 n.boolean(p.streaming_supported,'streaming capability');n.boolean(p.random_access_required,'random access');
 n.number(p.concurrency,'message concurrency',1);n.number(ctx.min_payload_bytes,'payload materiality',1);n.number(ctx.min_pressure_fraction,'memory pressure',0,1);
 n.records(p.allocations,'allocations');n.unique(p.allocations,'id');
 for(const a of p.allocations){n.requireValue(a.payload_digest===p.payload_digest,'Mixed payload allocation');n.number(a.bytes,'allocated bytes');n.number(a.start_ms,'allocation start');n.number(a.end_ms,'allocation end');n.requireValue(a.end_ms>=a.start_ms,'Negative allocation lifetime');n.requireValue(['serialized','decoded','other'].includes(a.representation),'Unknown allocation representation');}
 const points=p.allocations.flatMap(a=>[a.start_ms,a.end_ms]);let overlap=0;
 for(const t of points){const live=p.allocations.filter(a=>a.start_ms<=t&&a.end_ms>t),serialized=live.some(a=>a.representation==='serialized'),decoded=live.some(a=>a.representation==='decoded');if(serialized&&decoded)overlap=Math.max(overlap,live.reduce((sum,a)=>sum+a.bytes,0));}
 n.requireValue(overlap<=p.peak_bytes,'Attributed buffers exceed captured peak');
 if(p.payload_bytes<ctx.min_payload_bytes||!p.streaming_supported||p.random_access_required||!overlap||p.peak_bytes/p.limit_bytes<ctx.min_pressure_fraction)return [];
 return [n.finding(d,ctx,source,['memory_profile'],p.message_type+':'+p.operation+':materialization','An attributed allocation timeline shows overlapping whole-message representations under observed memory pressure.','Evaluate bounded streaming/chunking where consumer semantics permit. Preserve concurrency and capture boundaries; sampled peaks or payload size alone do not prove pressure.',[n.references.profile])];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-09',inspect),inspect};
