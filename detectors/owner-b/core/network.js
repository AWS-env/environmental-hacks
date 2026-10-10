'use strict';
const c=require('./contract'),j=require('./jobs');
const references={
 trace:'https://docs.aws.amazon.com/xray/latest/devguide/xray-api-segmentdocuments.html',
 cache:'https://www.rfc-editor.org/rfc/rfc9111.html',
 http:'https://www.rfc-editor.org/rfc/rfc9110.html',
 retry:'https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/',
 node:'https://nodejs.org/api/http.html#class-httpagent',
 har:'https://developer.chrome.com/docs/devtools/network/reference',
 binary:'https://protobuf.dev/programming-guides/encoding/',
 profile:'https://nodejs.org/api/inspector.html'
};
/** @param {any} input @param {string} key @param {Function} inspect */
function evaluateCheck(input,key,inspect){
 c.validate(input);j.requireValue(input.kind==='input'&&input.check_id===key,'Incorrect network dispatch');
 const result=c.envelope(input);
 for(const scope of input.scope){
  try{
   j.requireValue(input.detector_version==='1.0.0'&&input.context.rule_version==='network-1','Unsupported network version');
   j.requireValue(['candidate','runtime'].includes(input.context.mode),'Require candidate/runtime mode');
   const sources=input.sources.filter(s=>s.scope_id===scope);
   const source=j.structured(sources,'network-evidence-v1');
   const d=source.data;j.acquired(source,input);
   j.text(d.route,'route');j.text(d.capture.tool,'capture tool');j.text(d.capture.version,'capture version');
   j.requireValue(['synthetic','client','auditor'].includes(d.capture.origin),'Require capture origin');
   const findings=inspect(d,input.context,source,sources,input).map(f=>({...f,scope_id:scope,fingerprint:c.fingerprint(input.repository_id,key,scope,f.identity)}));
   result.findings.push(...findings);result.coverage.evaluated_scope.push(scope);
  }catch(e){result.coverage.limitations.push(scope+': '+e.message);}
 }
 result.status=result.coverage.evaluated_scope.length===input.scope.length?'completed':result.coverage.evaluated_scope.length?'partial':'unavailable';
 result.coverage.limitations.push('Bounded read-only evidence analysis. Client metadata/policy assertions require review; no inferred energy, carbon or savings. Sampled observations are not total workload counts.');
 if(input.context.mode==='candidate')result.coverage.limitations.push('Candidate only; completed coverage is not confirmation of runtime waste or human remediation approval.');
 c.validatePair(input,result);return result;
}
/** @param {any} d @param {any} context @param {any} source @param {string[]} fields @param {string} anchor @param {string} summary @param {string} advice @param {string[]} refs @param {string} [confidence] */
function finding(d,context,source,fields,anchor,summary,advice,refs,confidence='medium'){
 return j.finding(d.route+':'+anchor,summary,advice,fields.map(f=>j.cite(source,f)),refs,confidence);
}
/** @param {any} value */
function digest(value){j.requireValue(typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),'Require SHA256 identity');return value;}
/** @param {any} values @param {string} name */
function records(values,name){j.requireValue(Array.isArray(values)&&values.length>0&&values.length<=5000,'Missing/oversized '+name);return values;}
/** @param {any} spans */
function validateSpans(spans){
 records(spans,'spans');j.unique(spans,'id');const byId=new Map(spans.map(s=>[s.id,s]));
 for(const s of spans){
  j.text(s.request_group,'request group');j.text(s.destination,'destination');j.text(s.operation,'operation');
  j.number(s.start_ms,'start');j.number(s.end_ms,'end');j.requireValue(s.end_ms>=s.start_ms,'Negative span duration');
  j.requireValue(s.parent_id===null||typeof s.parent_id==='string','Missing parent identity');
  if(s.parent_id!==null)j.requireValue(byId.has(s.parent_id),'Missing parent span');
  const seen=new Set([s.id]);let parent=s.parent_id;
  while(parent!==null){j.requireValue(!seen.has(parent),'Cyclic trace');seen.add(parent);parent=byId.get(parent).parent_id;}
 }
 return spans;
}
module.exports={...j,evaluateCheck,finding,references,digest,records,validateSpans};
