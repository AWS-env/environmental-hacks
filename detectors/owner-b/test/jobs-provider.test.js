'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {collectMetrics,collectSchedules,collectJobLogs}=require('../collectors/job-provider');
const {processJobs}=require('../handlers/jobs-pipeline');
const {workers,runs,schedule}=require('./jobs-fixtures');
const window={start:'2026-10-01T00:00:00Z',end:'2026-10-01T01:00:00Z'};
const queries=['workers','busy_worker_seconds','queue_depth','arrivals'].map(Id=>({Id,MetricStat:{Metric:{Namespace:'Synthetic/Workers',MetricName:Id,Dimensions:[{Name:'Pool',Value:'unit'}]},Period:600,Stat:Id==='arrivals'||Id==='busy_worker_seconds'?'Sum':Id==='queue_depth'?'Maximum':'Average',Unit:Id==='busy_worker_seconds'?'Seconds':'Count'}}));
test('Worker collector aligns actual provider pages and never fills gaps with zero',async()=>{
  const expected=workers().sources[0].data.samples;let calls=0;
  const client={send:async cmd=>{assert.equal(cmd.constructor.name,'GetMetricDataCommand');calls++;return {MetricDataResults:queries.map(q=>({Id:q.Id,StatusCode:'Complete',Timestamps:expected.slice(calls===1?0:3,calls===1?3:6).map(x=>new Date(x.timestamp)),Values:expected.slice(calls===1?0:3,calls===1?3:6).map(x=>x[q.Id])})),...(calls===1?{NextToken:'second'}:{})};}};
  const d=await collectMetrics(window,queries,{pool_id:'unit'},client);assert.equal(calls,2);assert.deepEqual(d.samples,expected);assert.equal(d.acquisition.status,'complete');
  await assert.rejects(()=>collectMetrics(window,queries,{}, {send:async()=>({MetricDataResults:[]})}),/Missing metric sample/);
  await assert.rejects(()=>collectMetrics(window,queries,{}, {send:async()=>({MetricDataResults:[{Id:'workers',StatusCode:'PartialData'}]})}),/Incomplete/);
  await assert.rejects(()=>collectMetrics(window,queries,{}, {send:async()=>{throw new Error('AccessDenied');}}),/AccessDenied/);
});
test('Schedule collector describes every inventory item and preserves disabled/flexible state',async()=>{
  const calls=[],client={send:async cmd=>{calls.push(cmd.constructor.name);return cmd.constructor.name==='ListSchedulesCommand'?{Schedules:[{Name:'rebuild'}]}:{ScheduleExpression:'cron(0 1 * * ? *)',ScheduleExpressionTimezone:'Asia/Kolkata',State:'DISABLED',FlexibleTimeWindow:{Mode:'FLEXIBLE',MaximumWindowInMinutes:10}};}};
  const d=await collectSchedules('jobs',['jobs'],{rebuild:{pool_id:'pool'}},client);assert.deepEqual(calls,['ListSchedulesCommand','GetScheduleCommand']);assert.equal(d.jobs[0].enabled,false);assert.equal(d.jobs[0].flexible_window_seconds,600);
  await assert.rejects(()=>collectSchedules('not-allowed',['jobs'],{},client),/authorized/);
  await assert.rejects(()=>collectSchedules('jobs',['jobs'],{},client),/metadata/);
});
test('Job logs require complete bounded query, exact snapshot, and cancel on timeout',async()=>{
  const snapshot={repository_id:'synthetic',commit_sha:'a'.repeat(40),job_id:'rebuild'},event={...snapshot,run_id:'r1'};
  const client={send:async cmd=>cmd.constructor.name==='StartQueryCommand'?{queryId:'synthetic-query'}:{status:'Complete',statistics:{recordsMatched:1},results:[[{field:'@message',value:JSON.stringify({format:'job-run-event-v1',event})}]]}};
  const d=await collectJobLogs({...window,group:'jobs'},['jobs'],snapshot,client,async()=>{});assert.equal(d.runs.length,1);
  await assert.rejects(()=>collectJobLogs({...window,group:'jobs'},['jobs'],snapshot,{send:async cmd=>cmd.constructor.name==='StartQueryCommand'?{queryId:'synthetic'}:{status:'Complete',statistics:{recordsMatched:2000},results:[]}},async()=>{}),/Truncated/);
  const calls=[];await assert.rejects(()=>collectJobLogs({...window,group:'jobs'},['jobs'],snapshot,{send:async cmd=>{calls.push(cmd.constructor.name);return cmd.constructor.name==='StartQueryCommand'?{queryId:'synthetic'}:{status:'Running'};}},async()=>{}),/deadline/);assert.equal(calls.at(-1),'StopQueryCommand');
});
test('Jobs pipeline validates pairs, records unavailable acquisition, rejects mismatched families and storage/hub errors',async()=>{
  const stored=[],deps={bucket:'synthetic-bucket',hub:'',s3:{send:async cmd=>{stored.push(cmd.input);return {VersionId:'synthetic-version'};}}};
  const first=await processJobs('static',schedule(),{},deps),second=await processJobs('static',schedule(),{},deps);assert.equal(first.artifact.key,second.artifact.key);assert.equal(first.delivery.status,'blocked');assert.equal(stored[0].IfNoneMatch,'*');
  await assert.rejects(()=>processJobs('heuristic',schedule(),{},deps),/Unsupported/);
  await assert.rejects(()=>processJobs('static',schedule(),{},{...deps,s3:{send:async()=>{throw new Error('AccessDenied');}}}),/AccessDenied/);
  await assert.rejects(()=>processJobs('static',schedule(),{},{...deps,hub:'synthetic-hub',bus:{send:async()=>({FailedEntryCount:1,Entries:[{ErrorCode:'Denied'}]})}}),/publication/);
  const i=runs();i.sources=[];const result=await processJobs('log',{input:i,acquisition:{job_id:'missing',window:{...window,group:'jobs'}}},{},deps);assert.equal(result.result.status,'unavailable');assert.equal(result.result.findings.length,0);
});
