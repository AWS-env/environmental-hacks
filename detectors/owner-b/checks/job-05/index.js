'use strict';
const j=require('../../core/jobs');
function inspect(sources,input){
  j.requireValue(input.context.mode==='runtime','JOB-05 requires runtime observations');
  const source=j.structured(sources,'job-runs-v1','telemetry'),d=source.data,{start,end}=j.acquired(source,input);
  j.text(d.job_id,'job_id');j.unique(d.runs,'run_id');
  const minRuns=j.number(input.context.minimum_runs,'minimum_runs',2,100),minWork=j.number(input.context.minimum_work_units,'minimum_work_units',1);
  j.requireValue(Number.isInteger(minRuns)&&d.runs.length>=minRuns,'Insufficient job runs');
  const p=d.policy;j.requireValue(p&&p.reviewed===true,'Require reviewed job input boundary/change policy');
  for(const k of ['all_inputs_versioned','time_dependent','external_dependencies','reconciliation','cheap_change_check','hash_collision_safe'])j.boolean(p[k],k);
  const runs=[...d.runs].sort((a,b)=>j.instant(a.start)-j.instant(b.start));
  for(const r of runs){
    j.requireValue(r.job_id===d.job_id,'Job identity mismatch');
    const t=j.instant(r.start),u=j.instant(r.end);j.requireValue(t>=start&&u<=end&&t<u,'Job run outside acquisition window');
    j.text(r.input_version,'input_version');j.text(r.output_version,'output_version');j.text(r.work_unit,'work_unit');
    j.number(r.work_units,'work_units');j.number(r.attempt,'attempt',1);j.boolean(r.full_work,'full_work');
    j.requireValue(['success','failed','cancelled'].includes(r.outcome),'Unknown run outcome');
  }
  if(!p.all_inputs_versioned||p.time_dependent||p.external_dependencies||p.reconciliation||!p.cheap_change_check||!p.hash_collision_safe)return [];
  let repeated=0,identity=null;
  for(let i=1;i<runs.length;i++){
    const a=runs[i-1],b=runs[i];
    const repeat=a.outcome==='success'&&b.outcome==='success'&&a.attempt===1&&b.attempt===1&&a.full_work&&b.full_work&&a.work_units>=minWork&&b.work_units>=minWork&&a.input_version===b.input_version&&a.output_version===b.output_version&&a.work_unit===b.work_unit&&j.instant(a.end)<=j.instant(b.start);
    repeated=repeat?repeated+1:0;if(repeated>=minRuns-1){identity=d.job_id;break;}
  }
  if(!identity)return [];
  return [j.finding(identity,'Successful scheduled runs repeat full work across an unchanged, reviewed input boundary',
    'Add the reviewed cheap input-version check before full work; record the last successful version atomically. Preserve retry recovery and invalidate on every relevant input change. Do not skip reconciliation, expiry or external-state checks.',
    [j.cite(source,'runs'),j.cite(source,'policy')],['https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_software_a1.html','https://github.com/AWS-env/environmental-hacks/issues/183'])];
}
module.exports={evaluate:input=>j.evaluateCheck(input,'JOB-05',inspect)};
