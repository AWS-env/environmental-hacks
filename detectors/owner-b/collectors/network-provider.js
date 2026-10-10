'use strict';
const {XRayClient,GetTraceSummariesCommand,BatchGetTracesCommand}=require('@aws-sdk/client-xray');
const {CloudWatchClient,GetMetricDataCommand}=require('@aws-sdk/client-cloudwatch');
const n=require('../core/network'),{normalizeTraces}=require('../normalization/xray');
const {CloudWatchLogsClient,StartQueryCommand,GetQueryResultsCommand,StopQueryCommand}=require('@aws-sdk/client-cloudwatch-logs');
const {setTimeout:pause}=require('node:timers/promises');
const config={region:process.env.AWS_REGION||'ap-south-1',maxAttempts:2};
const AbortSignal=globalThis.AbortSignal;
const traceClient=new XRayClient(config),metricClient=new CloudWatchClient(config);
const logClient=new CloudWatchLogsClient(config);
/** @param {any} window @param {any} setup @param {any} snapshot @param {{send:Function}} [client] */
async function collectTraces(window,setup,snapshot,client=traceClient){
 const {start,end}=n.windowBounds(window,3600);n.text(setup.filter,'server trace filter');
 const ids=new Set(),receipts=[];let token;
 for(let page=0;page<20;page++){
  const r=await client.send(new GetTraceSummariesCommand({StartTime:new Date(start),EndTime:new Date(end),Sampling:false,FilterExpression:setup.filter,...(token?{NextToken:token}:{})}),{abortSignal:AbortSignal.timeout(15000)});
  receipts.push({operation:'GetTraceSummaries',request_id:r.$metadata?.requestId??null});
  for(const s of r.TraceSummaries||[]){ids.add(s.Id);n.requireValue(ids.size<=50,'Trace capture bound exceeded');}
  token=r.NextToken;if(!token)break;n.requireValue(page<19,'Trace pagination incomplete');
 }
 n.requireValue(ids.size>0,'No matching existing client traces');
 const traces=[];
 const all=[...ids];
 for(let i=0;i<all.length;i+=5){
  const r=await client.send(new BatchGetTracesCommand({TraceIds:all.slice(i,i+5)}),{abortSignal:AbortSignal.timeout(15000)});
  n.requireValue(!r.UnprocessedTraceIds?.length&&!r.NextToken,'Incomplete BatchGetTraces');
  n.requireValue((r.Traces||[]).length===all.slice(i,i+5).length,'Missing full trace');
  receipts.push({operation:'BatchGetTraces',request_id:r.$metadata?.requestId??null});traces.push(...r.Traces);
 }
 return normalizeTraces(traces,snapshot,{status:'complete',provider:'xray',...window,filter:setup.filter,receipts});
}
/** Exact server configured series; every expected point required, never fill zero.
 * @param {any} window @param {any} setup @param {any} snapshot @param {{send:Function}} [client] */
async function collectMetrics(window,setup,snapshot,client=metricClient){
 const {start,end}=n.windowBounds(window,3600),queries=setup.queries;
 n.requireValue(Array.isArray(queries)&&queries.length>0&&queries.length<=10,'Configure bounded metric queries');
 for(const q of queries)n.requireValue(q.MetricStat?.Metric&&!q.Expression&&!q.AccountId&&q.MetricStat.Period>=60,'Unsupported metric expression/period');
 const receipts=[],points={};let token;
 for(let page=0;page<8;page++){
  const r=await client.send(new GetMetricDataCommand({StartTime:new Date(start),EndTime:new Date(end),MetricDataQueries:queries,ScanBy:'TimestampAscending',MaxDatapoints:5000,...(token?{NextToken:token}:{})}),{abortSignal:AbortSignal.timeout(15000)});
  n.requireValue(!r.Messages?.length,'Metric provider warning');
  receipts.push({operation:'GetMetricData',request_id:r.$metadata?.requestId??null});
  for(const v of r.MetricDataResults||[]){
   n.requireValue(v.StatusCode==='Complete'&&!v.Messages?.length&&v.Timestamps?.length===v.Values?.length,'Incomplete metric query');
   if(!points[v.Id])points[v.Id]=new Map();
   for(let i=0;i<v.Values.length;i++){n.number(v.Values[i],'metric value');const t=new Date(v.Timestamps[i]).toISOString();n.requireValue(!points[v.Id].has(t)||points[v.Id].get(t)===v.Values[i],'Conflicting metric sample');points[v.Id].set(t,v.Values[i]);}
  }
  token=r.NextToken;if(!token)break;n.requireValue(page<7,'Metric pagination bound');
 }
 for(const q of queries){const period=q.MetricStat.Period*1000;n.requireValue(start%period===0&&end%period===0,'Align metric window to period');for(let t=start;t<end;t+=period)n.requireValue(points[q.Id]?.has(new Date(t).toISOString()),'Missing metric samples; not zero');}
 n.requireValue(setup.evidence?.commit_sha===snapshot.commit_sha,'Metric policy/header snapshot missing or stale');
 const data={...setup.evidence,format:'network-evidence-v1',...snapshot,route:snapshot.route,capture:{tool:'cloudwatch',version:'GetMetricData-v1',origin:'client'},metric_series:Object.fromEntries(Object.entries(points).map(([k,v])=>[k,[...v].map(([timestamp,value])=>({timestamp,value}))])),metric_definitions:queries,acquisition:{status:'complete',provider:'cloudwatch',...window,receipts}};
 const sum=id=>{const query=queries.find(q=>q.Id===id);n.requireValue(query&&query.MetricStat.Stat==='Sum'&&query.MetricStat.Unit==='Count','Counter binding requires Count/Sum');return [...points[id].values()].reduce((a,b)=>a+b,0);};
 if(setup.binding==='response-cache')data.cache_observations={eligible_requests:sum('eligible_requests'),cache_hits:sum('cache_hits'),origin_requests:sum('origin_requests'),identical_representation:setup.evidence.cache_observations?.identical_representation};
 else if(setup.binding==='connections')data.connection_observations={handler:setup.evidence.client_lifecycle?.handler,new_connections:sum('new_connections'),requests:sum('requests')};
 return data;
}
/** Existing normalized capture logs only; bounded query and cancellation, never enable customer logging.
 * @param {any} window @param {any} setup @param {any} snapshot @param {{send:Function}} [client] @param {Function} [wait] */
async function collectLogs(window,setup,snapshot,client=logClient,wait=()=>pause(1000)){
 const {start,end}=n.windowBounds(window,3600);n.text(setup.group,'authorized log group');
 const query='fields @message | filter format = "network-evidence-event-v1" and repository_id = '+JSON.stringify(snapshot.repository_id)+' and commit_sha = '+JSON.stringify(snapshot.commit_sha)+' and route = '+JSON.stringify(snapshot.route)+' | sort @timestamp asc | limit 2';
 const started=await client.send(new StartQueryCommand({logGroupName:setup.group,startTime:Math.floor(start/1000),endTime:Math.ceil(end/1000),queryString:query,limit:2}),{abortSignal:AbortSignal.timeout(15000)});n.text(started.queryId,'query ID');let terminal=false;
 try{
  for(let attempt=0;attempt<20;attempt++){
   const response=await client.send(new GetQueryResultsCommand({queryId:started.queryId}),{abortSignal:AbortSignal.timeout(15000)});
   if(response.status==='Complete'){
    terminal=true;n.requireValue(response.results?.length===1&&(response.statistics?.recordsMatched??1)===1,'Missing/ambiguous normalized log capture');
    const record=JSON.parse(response.results[0].find(r=>r.field==='@message').value);n.requireValue(record.format==='network-evidence-event-v1'&&record.repository_id===snapshot.repository_id&&record.commit_sha===snapshot.commit_sha&&record.route===snapshot.route,'Log capture identity mismatch');
    n.requireValue(record.data?.repository_id===snapshot.repository_id&&record.data?.commit_sha===snapshot.commit_sha&&record.data?.route===snapshot.route,'Log payload snapshot mismatch');
    return {...record.data,acquisition:{status:'complete',provider:'cloudwatch-logs',...window,query,query_id:started.queryId,statistics:response.statistics||{}}};
   }
   n.requireValue(['Scheduled','Running'].includes(response.status),'Log query failed');await wait();
  }
  throw new Error('Log query deadline');
 }finally{if(!terminal)await client.send(new StopQueryCommand({queryId:started.queryId}));}
}
module.exports={collectTraces,collectMetrics,collectLogs};
