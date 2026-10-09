'use strict';
const {validate,validatePair,envelope,fingerprint,canonical,splitLines}=require('./contract');
const VERSION='1.0.0';
function requireValue(ok,message){if(!ok)throw new Error(message);}
function text(v,name){requireValue(typeof v==='string'&&v.length>0&&v.length<=512,'Missing/invalid '+name);return v;}
function number(v,name,min=0,max=Number.MAX_SAFE_INTEGER){requireValue(typeof v==='number'&&Number.isFinite(v)&&v>=min&&v<=max,'Missing/invalid '+name);return v;}
function boolean(v,name){requireValue(typeof v==='boolean','Missing/invalid '+name);return v;}
function instant(v){text(v,'timestamp');const n=Date.parse(v);requireValue(Number.isFinite(n)&&/(Z|[+-]\d\d:\d\d)$/.test(v),'Require timezone-qualified timestamp');return n;}
function windowBounds(w,maxSeconds=604800){requireValue(w&&typeof w==='object','Missing window');const start=instant(w.start),end=instant(w.end);requireValue(start<end&&(end-start)/1000<=maxSeconds,'Invalid/oversized window');return {start,end};}
function unique(items,key){requireValue(Array.isArray(items)&&items.length<=5000,'Invalid/oversized records');requireValue(new Set(items.map(x=>text(x[key],key))).size===items.length,'Duplicate '+key);}
function cite(source,field){return {source_id:source.source_id,kind:source.kind,locator:source.locator,field,value:source.data[field]};}
function line(source,start,end=start){const lines=splitLines(source.content);return {source_id:source.source_id,kind:'static',locator:source.locator,line_start:start,value:lines.slice(start-1,end).join('\n')};}
function structured(sources,format,kind){const matches=sources.filter(s=>s.kind!=='static'&&s.data.format===format&&(!kind||s.kind===kind));requireValue(matches.length===1,'Require one '+format+' source');return matches[0];}
function acquired(source,input){const d=source.data;requireValue(d.acquisition?.status==='complete','Acquisition incomplete or unavailable: '+(d.acquisition?.reason||'no provider receipt'));requireValue(d.repository_id===input.repository_id&&d.commit_sha===input.commit_sha,'Telemetry snapshot mismatch');return windowBounds(d.acquisition);}
/** Each check calls this adapter only after evaluating the whole scope. */
function evaluateCheck(input,key,inspect){
  validate(input);requireValue(input.kind==='input'&&input.check_id===key,'Incorrect check dispatch');
  const result=envelope(input);
  for(const scope of input.scope){
    try{
      requireValue(input.detector_version===VERSION&&input.context.rule_version==='jobs-1','Unsupported detector/rule version');
      requireValue(['candidate','runtime'].includes(input.context.mode),'Require explicit candidate/runtime mode');
      const sources=input.sources.filter(s=>s.scope_id===scope);
      const candidates=inspect(sources,input);
      const findings=candidates.map(c=>({...c,scope_id:scope,fingerprint:fingerprint(input.repository_id,key,scope,c.identity)}));
      result.findings.push(...findings);result.coverage.evaluated_scope.push(scope);
    }catch(e){result.coverage.limitations.push(scope+': '+e.message);}
  }
  result.status=result.coverage.evaluated_scope.length===input.scope.length?'completed':result.coverage.evaluated_scope.length?'partial':'unavailable';
  result.coverage.limitations.push('Read-only bounded evidence analysis; connector policy assertions require review. No inferred CPU, energy or carbon savings.');
  if(input.context.mode==='candidate')result.coverage.limitations.push('Candidate evaluation only; completed coverage does not confirm runtime waste or remediation approval.');
  validatePair(input,result);return result;
}
function finding(identity,summary,recommendation,evidence,refs,confidence='medium'){return {identity,summary,recommendation,evidence,references:refs,confidence};}
module.exports={VERSION,requireValue,text,number,boolean,instant,windowBounds,unique,cite,line,structured,acquired,evaluateCheck,finding,canonical};
