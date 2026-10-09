'use strict';
const n=require('../../core/network');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const p=d.conversion_profile;n.requireValue(p.format_version===1&&p.complete===true,'Incomplete/unknown conversion profile');
 n.text(p.flow,'flow identity');n.text(p.message_type,'message type');n.requireValue(p.time_unit==='cpu-milliseconds'&&p.capture_kind==='attributed-cpu','Attributed CPU capture required');
 n.number(ctx.min_conversion_cpu_ms,'CPU materiality',0.001);const steps=n.records(p.steps,'conversion steps');n.unique(steps,'id');
 for(const s of steps){n.requireValue(['parse','serialize'].includes(s.operation),'Unknown conversion');n.digest(s.semantic_digest);n.text(s.callsite,'conversion callsite');n.number(s.cpu_ms,'conversion CPU');n.boolean(s.boundary_requires_bytes,'protocol boundary');n.boolean(s.mutates,'payload mutation');n.text(s.encoding,'encoding');}
 let waste=0;
 for(let i=0;i<steps.length-2;i++){
  const a=steps[i],b=steps[i+1],c=steps[i+2];
  if(a.operation==='parse'&&b.operation==='serialize'&&c.operation==='parse'&&a.semantic_digest===b.semantic_digest&&b.semantic_digest===c.semantic_digest&&a.encoding===b.encoding&&b.encoding===c.encoding&&!a.mutates&&!b.mutates&&!c.mutates&&!b.boundary_requires_bytes&&!c.boundary_requires_bytes)waste+=b.cpu_ms+c.cpu_ms;
 }
 if(waste<ctx.min_conversion_cpu_ms)return [];
 return [n.finding(d,ctx,source,['conversion_profile'],p.message_type+':'+p.flow+':conversion-chain','Equivalent unmutated content is serialized and reparsed between local layers with attributed CPU work.','Pass the parsed representation across compatible local layers. Preserve mandatory network/process/signature boundaries and encoding/precision changes; no customer code is rewritten.',[n.references.profile])];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-12',inspect),inspect};
