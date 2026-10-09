'use strict';
const n=require('../core/network');
/** X-Ray owner_b metadata is an explicit connector contract, not inferred application semantics.
 * @param {any[]} traces @param {any} snapshot @param {any} acquisition */
function normalizeTraces(traces,snapshot,acquisition){
 const spans=[],policies=[],origins=new Set();let complete=true;
 const walk=(s,traceId,parent=null,depth=0)=>{
  n.requireValue(depth<32&&spans.length<2000,'Trace nesting/count bound');
  n.text(s.id,'X-Ray span ID');
  if(s.in_progress||typeof s.end_time!=='number'){complete=false;return;}
  const m=s.metadata?.owner_b;
  if(m){
   n.requireValue(m.repository_id===snapshot.repository_id&&m.commit_sha===snapshot.commit_sha,'X-Ray snapshot mismatch');
   n.requireValue(m.route===snapshot.route,'X-Ray route mismatch');
   n.requireValue(['client','auditor','synthetic'].includes(m.capture_origin),'Unknown trace capture origin');origins.add(m.capture_origin);
   if(m.batch_policy)policies.push(m.batch_policy);
   spans.push({...m,id:s.id,parent_id:parent,request_group:traceId,start_ms:s.start_time*1000,end_ms:s.end_time*1000,...(s.http?.response?.content_length!==undefined?{bytes:s.http.response.content_length}:{}),status:s.http?.response?.status});
  }else if(parent!==null)complete=false;
  for(const child of s.subsegments||[])walk(child,traceId,m?s.id:parent,depth+1);
 };
 for(const t of traces){if(t.LimitExceeded)complete=false;for(const segment of t.Segments||[]){const raw=JSON.parse(segment.Document);walk(raw,t.Id,raw.parent_id||null);}}
 n.requireValue(spans.length>0,'No snapshot-correlated owner_b trace metadata');
 const policy=policies[0];if(policy)n.requireValue(policies.every(p=>n.canonical(p)===n.canonical(policy)),'Conflicting trace policy');
 n.requireValue(origins.size===1,'Mixed trace capture origins');
 return {format:'network-evidence-v1',...snapshot,route:snapshot.route,capture:{tool:'aws-xray',version:'segment-v1',origin:[...origins][0]},spans,trace:{complete,sampled:true},...(policy?{batch_policy:policy}:{}),acquisition};
}
module.exports={normalizeTraces};
