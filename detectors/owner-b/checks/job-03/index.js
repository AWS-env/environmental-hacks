'use strict';
const j=require('../../core/jobs');
function inspect(sources,input){
  j.requireValue(input.context.mode==='runtime','JOB-03 requires runtime observations');
  const source=j.structured(sources,'worker-capacity-v1','telemetry'),d=source.data,{start,end}=j.acquired(source,input);
  j.text(d.pool_id,'pool_id');const period=j.number(d.period_seconds,'period_seconds',60,3600);
  const minimum=j.number(input.context.minimum_window_seconds,'minimum_window_seconds',3600,604800),maxBusy=j.number(input.context.max_busy_fraction,'max_busy_fraction',0,0.2);
  j.requireValue((end-start)/1000>=minimum&&Number.isInteger(period)&&(end-start)/1000%period===0,'Insufficient or misaligned representative window');
  const p=d.policy;j.requireValue(p&&p.reviewed===true,'Require reviewed capacity/SLA policy');
  j.number(p.minimum_workers,'minimum_workers');j.number(p.burst_reserve_workers,'burst_reserve_workers');
  j.boolean(p.shared_workload,'shared_workload');j.boolean(p.leader_or_heartbeat_duties,'leader_or_heartbeat_duties');j.boolean(p.cold_start_acceptable,'cold_start_acceptable');
  j.unique(d.samples,'timestamp');j.requireValue(d.samples.length===(end-start)/1000/period,'Missing metric samples');
  const samples=[...d.samples].sort((a,b)=>j.instant(a.timestamp)-j.instant(b.timestamp));
  const reserve=p.minimum_workers+p.burst_reserve_workers;
  let idle=true;
  for(let i=0;i<samples.length;i++){
    const s=samples[i];j.requireValue(j.instant(s.timestamp)===start+i*period*1000,'Metric gaps or incompatible windows');
    const cap=j.number(s.workers,'workers'),busy=j.number(s.busy_worker_seconds,'busy_worker_seconds',0,cap*period);
    j.number(s.queue_depth,'queue_depth');j.number(s.arrivals,'arrivals');
    if(cap<=reserve||cap===0||s.queue_depth>0||busy/(cap*period)>maxBusy)idle=false;
  }
  if(p.shared_workload||p.leader_or_heartbeat_duties||!p.cold_start_acceptable||!idle)return [];
  return [j.finding(d.pool_id,'Sustained provisioned worker capacity exceeds the reviewed reserve while observed work stays low',
    'Review queue-driven scaling and minimum capacity against burst arrivals and recovery SLA. Busy-worker seconds describe application occupancy, not measured CPU or electricity. Do not automatically scale to zero.',
    [j.cite(source,'samples'),j.cite(source,'policy')],['https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_hardware_a3.html','https://github.com/AWS-env/environmental-hacks/issues/181'])];
}
module.exports={evaluate:input=>j.evaluateCheck(input,'JOB-03',inspect)};
