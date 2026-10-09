'use strict';
const {CloudWatchClient,GetMetricDataCommand}=require('@aws-sdk/client-cloudwatch');
const {SchedulerClient,ListSchedulesCommand,GetScheduleCommand}=require('@aws-sdk/client-scheduler');
const {CloudWatchLogsClient,StartQueryCommand,GetQueryResultsCommand,StopQueryCommand}=require('@aws-sdk/client-cloudwatch-logs');
const {setTimeout:delay}=require('node:timers/promises');
const j=require('../core/jobs');
const config={region:process.env.AWS_REGION||'ap-south-1',maxAttempts:2};
const metricClient=new CloudWatchClient(config),scheduleClient=new SchedulerClient(config),logsClient=new CloudWatchLogsClient(config);
/** Only server configured metric queries can be read; request supplies window/identity, not arbitrary metric expressions.
 * @param {any} window @param {any[]} queries @param {any} snapshot @param {{send:Function}} [client] */
async function collectMetrics(window,queries,snapshot,client=metricClient){
  const {start,end}=j.windowBounds(window,86400);j.requireValue(Array.isArray(queries)&&queries.length===4,'Configure four worker metric queries');
  const keys=['workers','busy_worker_seconds','queue_depth','arrivals'];
  j.requireValue(j.canonical(queries.map(q=>q.Id).sort())===j.canonical([...keys].sort()),'Invalid worker query IDs');
  const periods=new Set(queries.map(q=>q.MetricStat?.Period));j.requireValue(periods.size===1,'Incompatible metric periods');
  const period=queries[0].MetricStat.Period;j.requireValue(Number.isInteger(period)&&period>=60&&period<=3600,'Invalid metric period');
  const expectedStats={workers:'Average',busy_worker_seconds:'Sum',queue_depth:'Maximum',arrivals:'Sum'};
  const expectedUnits={workers:'Count',busy_worker_seconds:'Seconds',queue_depth:'Count',arrivals:'Count'};
  const definitions=queries.map(q=>{j.requireValue(!q.Expression&&!q.AccountId&&q.MetricStat?.Metric&&!q.MetricStat.Metric.Namespace.startsWith('AWS/'),'Require explicitly configured application occupancy metrics');j.requireValue(q.MetricStat.Stat===expectedStats[q.Id]&&q.MetricStat.Unit===expectedUnits[q.Id],'Incorrect metric statistic/unit');return {...q,ReturnData:true};});
  const scopes=new Set(definitions.map(q=>j.canonical([q.MetricStat.Metric.Namespace,[...(q.MetricStat.Metric.Dimensions||[])].sort((a,b)=>a.Name.localeCompare(b.Name))])));
  j.requireValue(scopes.size===1,'Metric namespace/dimensions mismatch');
  const records=Object.fromEntries(keys.map(k=>[k,new Map()])),receipts=[];
  let token;
  for(let page=0;page<8;page++){
    const r=await client.send(new GetMetricDataCommand({StartTime:new Date(start),EndTime:new Date(end),MetricDataQueries:definitions,ScanBy:'TimestampAscending',MaxDatapoints:5000,...(token?{NextToken:token}:{})}));
    j.requireValue(!r.Messages?.length,'Provider metric warning');
    receipts.push({request_id:r.$metadata?.requestId??null,statuses:(r.MetricDataResults||[]).map(x=>({id:x.Id,status:x.StatusCode,messages:x.Messages||[]}))});
    for(const v of r.MetricDataResults||[]){
      j.requireValue(keys.includes(v.Id)&&v.StatusCode==='Complete'&&!v.Messages?.length,'Incomplete metric query');
      j.requireValue(v.Timestamps?.length===v.Values?.length,'Metric timestamp/value mismatch');
      for(let i=0;i<v.Values.length;i++){
        const t=new Date(v.Timestamps[i]).toISOString();j.number(v.Values[i],'metric value');
        const old=records[v.Id].get(t);j.requireValue(old===undefined||old===v.Values[i],'Conflicting metric point');records[v.Id].set(t,v.Values[i]);
      }
    }
    token=r.NextToken;if(!token)break;j.requireValue(page<7,'Metric pagination bound exceeded');
  }
  const samples=[];
  for(let t=start;t<end;t+=period*1000){const timestamp=new Date(t).toISOString(),s={timestamp};for(const k of keys){j.requireValue(records[k].has(timestamp),'Missing metric sample; not idle');s[k]=records[k].get(timestamp);}samples.push(s);}
  return {format:'worker-capacity-v1',...snapshot,period_seconds:period,samples,acquisition:{status:'complete',provider:'cloudwatch',...window,queries:definitions,receipts}};
}
/** Reads authorized group, then describes every selected schedule. Pool/duration policy remains a reviewed connector assertion.
 * @param {string} group @param {string[]} allowlist @param {any} metadata @param {{send:Function}} [client] */
async function collectSchedules(group,allowlist,metadata,client=scheduleClient){
  j.requireValue(allowlist.includes(group),'Schedule group not authorized');const jobs=[],receipts=[];let token;
  for(let page=0;page<5;page++){
    const r=await client.send(new ListSchedulesCommand({GroupName:group,MaxResults:50,...(token?{NextToken:token}:{})}));
    receipts.push({operation:'ListSchedules',request_id:r.$metadata?.requestId??null});
    for(const item of r.Schedules||[]){
      j.requireValue(jobs.length<50,'Inventory bound exceeded');
      const d=await client.send(new GetScheduleCommand({GroupName:group,Name:item.Name}));
      receipts.push({operation:'GetSchedule',request_id:d.$metadata?.requestId??null});
      j.requireValue(metadata[item.Name],'Missing reviewed schedule pool/capacity metadata');
      jobs.push({...metadata[item.Name],job_id:group+'/'+item.Name,expression:d.ScheduleExpression,dialect:'aws-scheduler',timezone:d.ScheduleExpressionTimezone||'UTC',enabled:d.State==='ENABLED',flexible_window_seconds:d.FlexibleTimeWindow?.Mode==='OFF'?0:(d.FlexibleTimeWindow?.MaximumWindowInMinutes??1)*60,...(d.StartDate?{start:new Date(d.StartDate).toISOString()}:{}),...(d.EndDate?{end:new Date(d.EndDate).toISOString()}:{})});
    }
    token=r.NextToken;if(!token)break;j.requireValue(page<4,'Schedule pagination bound exceeded');
  }
  return {format:'schedule-inventory-v1',complete:true,jobs,acquisition:{status:'complete',provider:'eventbridge-scheduler',group,receipts}};
}
/** @param {any} window @param {string[]} allowlist @param {any} snapshot @param {{send:Function}} [client] @param {()=>Promise<void>} [pause] */
async function collectJobLogs(window,allowlist,snapshot,client=logsClient,pause=()=>delay(1000)){
  const {start,end}=j.windowBounds(window,3600);j.requireValue(allowlist.includes(window.group),'Job log group not authorized');
  j.requireValue(start%1000===0&&end%1000===0,'Integral-second log window required');
  const query='fields @message | filter format = "job-run-event-v1"'+Object.entries(snapshot).map(([k,v])=>' and event.'+k+' = '+JSON.stringify(v)).join('')+' | sort @timestamp asc | limit 1000';
  const r=await client.send(new StartQueryCommand({logGroupName:window.group,startTime:start/1000,endTime:end/1000,queryString:query,limit:1000}));
  j.requireValue(r.queryId,'Missing query ID');let terminal=false;
  try{
    for(let i=0;i<20;i++){
      const result=await client.send(new GetQueryResultsCommand({queryId:r.queryId}));
      if(result.status==='Complete'){
        terminal=true;const rows=result.results||[];j.requireValue(rows.length<1000&&(result.statistics?.recordsMatched??rows.length)<=rows.length,'Truncated job acquisition');
        const runs=rows.map(row=>{const value=row.find(x=>x.field==='@message')?.value;j.requireValue(value,'Missing event JSON');const msg=JSON.parse(value);j.requireValue(msg.format==='job-run-event-v1'&&Object.entries(snapshot).every(([k,v])=>msg.event?.[k]===v),'Job snapshot mismatch');return msg.event;});
        const byId=new Map();for(const e of runs){j.text(e.run_id,'run_id');j.requireValue(!byId.has(e.run_id)||j.canonical(byId.get(e.run_id))===j.canonical(e),'Conflicting duplicate run');byId.set(e.run_id,e);}
        return {format:'job-runs-v1',...snapshot,runs:[...byId.values()],acquisition:{status:'complete',provider:'cloudwatch-logs-insights',...window,query,query_id:r.queryId,statistics:result.statistics||{}}};
      }
      j.requireValue(['Running','Scheduled'].includes(result.status),'Job query '+result.status);await pause();
    }
    throw new Error('Job query deadline exceeded');
  }finally{if(!terminal)await client.send(new StopQueryCommand({queryId:r.queryId}));}
}
module.exports={collectMetrics,collectSchedules,collectJobLogs};
