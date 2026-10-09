'use strict';
const j=require('../../core/jobs'),schedules=require('../../normalization/schedules');
function inspect(sources,input){
  const candidates=sources.filter(s=>s.kind==='static'||s.data.format==='schedule-inventory-v1');
  j.requireValue(candidates.length===1,'Require one complete schedule inventory per pool scope');
  const source=candidates[0],{jobs,start,end}=schedules.normalize(source,input.context),out=[];
  for(let a=0;a<jobs.length;a++)for(let b=a+1;b<jobs.length;b++){
    const x=jobs[a],y=jobs[b];if(x.pool_id!==y.pool_id)continue;
    j.requireValue(x.capacity===y.capacity,'Inconsistent pool capacity');
    if(x.intentional_alignment||y.intentional_alignment||x.serialized||y.serialized||x.slots+y.slots<=x.capacity)continue;
    let match=null;
    for(const t of x.times){for(const u of y.times){
      const known=x.duration!==null&&y.duration!==null;
      // For AWS, minute precision does not prove exact start times. Potential overlap is a candidate.
      if(known?Math.max(t,u,start)<Math.min(t+x.duration*1000+(x.dialect==='aws-scheduler'?60000:0),u+y.duration*1000+(y.dialect==='aws-scheduler'?60000:0),end):t===u&&t>=start){match={t,u,known};break;}
    }if(match)break;}
    if(!match)continue;
    const identity=j.canonical([x.pool_id,...[x.job_id,y.job_id].sort()]);
    const evidence=source.kind==='static'?[j.line(source,1,source.content.split(/\r?\n/).length)]:[j.cite(source,'jobs')];
    out.push(j.finding(identity,`Schedule collision candidate: ${x.job_id} and ${y.job_id} ${match.known?'may overlap':'have coincident scheduled minutes'} on constrained pool ${x.pool_id}`,
      'Review observed durations, deadlines and dependencies before staggering these jobs or enabling a flexible window. This inventory does not prove a runtime load spike.',evidence,[schedules.REF,'https://github.com/AWS-env/environmental-hacks/issues/182']));
  }
  return out.sort((a,b)=>a.identity.localeCompare(b.identity));
}
module.exports={evaluate:input=>j.evaluateCheck(input,'JOB-04',inspect)};
