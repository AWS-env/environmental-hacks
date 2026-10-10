'use strict';
const n=require('../../core/network');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 n.validateSpans(d.spans);n.requireValue(d.trace.complete===true,'Incomplete trace correlation');
 const policy=d.batch_policy;n.text(policy.documentation,'batch documentation');n.requireValue(/^https:\/\//.test(policy.documentation),'Invalid batch reference');
 n.boolean(policy.supported,'batch support');n.boolean(policy.required_streaming,'streaming semantics');
 n.number(ctx.min_calls,'min_calls',2);n.number(ctx.max_small_bytes,'max_small_bytes',1);
 const groups=new Map();
 for(const s of d.spans.filter(s=>s.role==='outbound')){
  n.text(s.authorization_key,'authorization identity');n.number(s.bytes,'payload bytes');n.number(s.setup_ms,'setup timing');
  n.requireValue(Array.isArray(s.dependencies),'Missing operation dependencies');
  const key=n.canonical([s.request_group,s.destination,s.operation,s.authorization_key]);
  if(!groups.has(key))groups.set(key,[]);groups.get(key).push(s);
 }
 n.requireValue(groups.size>0,'No outbound evidence');
 const found=[];
 for(const calls of groups.values()){
  if(!policy.supported||policy.required_streaming||calls.some(s=>s.dependencies.length)||calls.length<ctx.min_calls)continue;
  if(calls.some(s=>s.bytes>ctx.max_small_bytes)||calls.reduce((a,s)=>a+s.setup_ms,0)<=0)continue;
  const first=calls[0];
  found.push(n.finding(d,ctx,source,['spans','trace','batch_policy'],first.destination+':'+first.operation+':batch',
   'Many independent small remote calls incur observed setup work.',
   'Use the documented bounded batch operation; preserve authorization, error isolation and payload limits.',[n.references.trace,policy.documentation]));
 }
 return [...new Map(found.map(f=>[f.identity,f])).values()];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-01',inspect),inspect};
