'use strict';
const crypto=require('node:crypto');
const commit='a'.repeat(40),repository='synthetic:job-fixtures';
const start='2026-10-01T00:00:00Z',end='2026-10-01T01:00:00Z';
function base(check,mode='runtime'){return {schema_version:'1.0',kind:'input',repository_id:repository,scan_id:'synthetic-'+check,commit_sha:commit,check_id:check,detector_version:'1.0.0',context:{rule_version:'jobs-1',mode},scope:['unit'],sources:[]};}
function source(format,data,kind='telemetry'){return {source_id:'observations',scope_id:'unit',kind,locator:'synthetic:'+format,data:{format,repository_id:repository,commit_sha:commit,acquisition:{status:'complete',start,end,provider:'synthetic-fixture'},...data}};}
function schedule(){
  const input=base('JOB-04','candidate');input.context.horizon={start,end};input.context.max_occurrences=100;
  const job=id=>({job_id:id,pool_id:'pool',pool_capacity:1,slots:1,enabled:true,expression:'0 * * * *',dialect:'posix',timezone:'UTC',duration_seconds:600,flexible_window_seconds:0,jitter_seconds:0,intentional_alignment:false,serialized:false});
  input.sources=[source('schedule-inventory-v1',{complete:true,jobs:[job('build'),job('sync')]},'artifact')];return input;
}
function workers(){
  const input=base('JOB-03');input.context.minimum_window_seconds=3600;input.context.max_busy_fraction=0.05;
  input.sources=[source('worker-capacity-v1',{pool_id:'workers',period_seconds:600,policy:{reviewed:true,minimum_workers:0,burst_reserve_workers:0,shared_workload:false,leader_or_heartbeat_duties:false,cold_start_acceptable:true},samples:Array.from({length:6},(_,i)=>({timestamp:new Date(Date.parse(start)+i*600000).toISOString(),workers:4,busy_worker_seconds:10,queue_depth:0,arrivals:1}))})];return input;
}
function runs(){
  const input=base('JOB-05');input.context.minimum_runs=3;input.context.minimum_work_units=100;
  input.sources=[source('job-runs-v1',{job_id:'rebuild',policy:{reviewed:true,all_inputs_versioned:true,time_dependent:false,external_dependencies:false,reconciliation:false,cheap_change_check:true,hash_collision_safe:true},runs:Array.from({length:3},(_,i)=>({run_id:'run-'+i,job_id:'rebuild',start:new Date(Date.parse(start)+i*600000).toISOString(),end:new Date(Date.parse(start)+i*600000+120000).toISOString(),input_version:'input-1',output_version:'output-1',work_unit:'records',work_units:500,attempt:1,full_work:true,outcome:'success'}))})];return input;
}
function request(check='JOB-01',mode='runtime'){
  const input=base(check,mode);input.context.minimum_requests=3;input.context.minimum_operation_ms=100;input.context.minimum_peak_mean_ratio=3;input.context.minimum_backpressure_ms=100;
  const content='export async function handler(req, res) {\n  await report.generate(req);\n  return res.send("accepted");\n}\n';
  const digest=crypto.createHash('sha256').update(content).digest('hex');
  input.sources=[{source_id:'code',scope_id:'unit',kind:'static',locator:'handler.js',content},source('request-work-v1',{locator:'handler.js',source_sha256:digest,language:'javascript-es2022',route_id:'POST /reports',handler:'handler',operation:'report.generate',response_call:'res.send',sender:'web',receiver:'report-service',complete:true,async_completion_acceptable:true,requires_transaction_completion:false,heavy_operation:true,existing_decoupling:false,metadata_reviewed:true,deployment_inventory_complete:true},'artifact')];
  if(mode==='runtime'||check==='JOB-06')input.sources.push({...source('request-observations-v1',{route_id:'POST /reports',operation:'report.generate',source_sha256:digest,peak_arrivals_per_minute:100,mean_arrivals_per_minute:10,events:Array.from({length:3},(_,i)=>({request_id:'req-'+i,operation_start:new Date(Date.parse(start)+i*60000).toISOString(),operation_end:new Date(Date.parse(start)+i*60000+1000).toISOString(),response_time:new Date(Date.parse(start)+i*60000+1100).toISOString(),operation_ms:1000,backpressure_ms:200,downstream_failures:0}))}),source_id:'requests'});
  return input;
}
const factories={'JOB-04':schedule,'JOB-03':workers,'JOB-05':runs,'JOB-01':()=>request(),'JOB-06':()=>request('JOB-06')};
module.exports={base,source,schedule,workers,runs,request,factories};
