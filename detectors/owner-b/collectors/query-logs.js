'use strict';
const {CloudWatchLogsClient,StartQueryCommand,GetQueryResultsCommand,StopQueryCommand}=require('@aws-sdk/client-cloudwatch-logs');
const {setTimeout:delay}=require('node:timers/promises');
const logs=new CloudWatchLogsClient({region:process.env.AWS_REGION||'eu-north-1',maxAttempts:2});
const LIMIT=1000,MAX_QUERIES=15,MAX_EVENTS=5000;
/** Bounded read-only connector; caller supplies only an allowlisted group and <=1-hour window.
 * @param {{group:string,start:string,end:string}} window
 * @param {string[]} allowlist
 * @param {{repository_id:string,commit_sha:string,locator:string,source_sha256:string}} snapshot
 * @param {{send:Function}} [client]
 * @param {()=>Promise<void>} [pause]
 */
async function collect(window,allowlist,snapshot,client=logs,pause=()=>delay(1000)) {
  if(!window || !allowlist.includes(window.group))throw new Error('Query log group is not configured/authorized');
  const start=Date.parse(window.start)/1000,end=Date.parse(window.end)/1000;
  if(!Number.isInteger(start)||!Number.isInteger(end)||start>=end||end-start>3600||!/(Z|[+-]\d\d:\d\d)$/.test(window.start)||!/(Z|[+-]\d\d:\d\d)$/.test(window.end))throw new Error('Require timezone-qualified integral-second window <=1 hour');
  const query='fields @message | filter format = "query-event-v1"'+Object.entries(snapshot).map(([k,v])=>' and event.'+k+' = '+JSON.stringify(v)).join('')+' | sort @timestamp asc | limit '+LIMIT;
  const receipts=[],records=new Map();
  const deadline=Date.now()+45000;
  let queryCount=0;
  async function partition(a,b,depth) {
    if(Date.now()>=deadline)throw new Error('Overall acquisition deadline exceeded');
    if(++queryCount>MAX_QUERIES)throw new Error('Acquisition exceeds bounded query count');
    const started=await client.send(new StartQueryCommand({logGroupName:window.group,startTime:a,endTime:b,queryString:query,limit:LIMIT}));
    if(!started.queryId)throw new Error('StartQuery returned no query ID');
    let terminal=false;
    try {
      let response;
      for(let i=0;i<20;i++) {
        if(Date.now()>=deadline)throw new Error('Overall acquisition deadline exceeded');
        response=await client.send(new GetQueryResultsCommand({queryId:started.queryId}));
        if(response.status==='Complete') {terminal=true;break;}
        if(!['Scheduled','Running'].includes(response.status)) {terminal=true;throw new Error('Logs query '+response.status);}
        await pause();
      }
      if(!terminal)throw new Error('Logs query polling deadline exceeded');
      receipts.push({query_id:started.queryId,start:a,end:b,status:response.status,statistics:response.statistics});
      const rows=response.results||[];
      if(rows.length>=LIMIT||response.statistics?.recordsMatched>rows.length) {
        if(depth>=3||b-a<=1)throw new Error('Truncated acquisition cannot certify coverage');
        const mid=Math.floor((a+b)/2);await partition(a,mid,depth+1);await partition(mid,b,depth+1);return;
      }
      for(const row of rows) {
        const message=row.find(f=>f.field==='@message')?.value;
        if(!message)throw new Error('Missing log message');
        let parsed;try {parsed=JSON.parse(message);}catch {throw new Error('Malformed JSON query log');}
        if(parsed.format!=='query-event-v1'||!parsed.event?.event_id)throw new Error('Malformed normalized query log');
        const e=parsed.event;
        if(Object.entries(snapshot).some(([k,v])=>e[k]!==v))throw new Error('Log query snapshot mismatch');
        if(records.has(e.event_id)&&JSON.stringify(records.get(e.event_id))!==JSON.stringify(e))throw new Error('Conflicting duplicate event');
        records.set(e.event_id,e);
        if(records.size>MAX_EVENTS)throw new Error('Too many query events');
      }
    } finally {
      if(!terminal)await client.send(new StopQueryCommand({queryId:started.queryId}));
    }
  }
  await partition(start,end,0);
  return {format:'query-event-v1',acquisition:{status:'complete',start:window.start,end:window.end,provider:'cloudwatch-logs-insights',group:window.group,query:query,receipts},events:[...records.values()].sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp)||a.event_id.localeCompare(b.event_id))};
}
module.exports={collect};
