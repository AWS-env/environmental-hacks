'use strict';
const n=require('../../core/network');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 n.requireValue(d.trace.complete===true,'Incomplete attempts');
 const attempts=n.records(d.attempts,'attempts');n.unique(attempts,'id');const groups=new Map();
 n.number(ctx.min_retry_attempts,'minimum attempts',3);n.number(ctx.min_retry_delay_ms,'minimum delay',1);
 for(const a of attempts){
  n.text(a.retry_group,'retry group');n.text(a.destination,'destination');n.text(a.operation,'operation');n.text(a.authorization_key,'authorization identity');
  n.number(a.attempt,'attempt',1);n.requireValue(Number.isInteger(a.attempt),'Attempt must be integral');
  n.number(a.start_ms,'attempt start');n.number(a.end_ms,'attempt end');n.requireValue(a.end_ms>=a.start_ms,'Negative attempt duration');
  n.number(a.status,'status',100,599);n.boolean(a.client_retry,'client retry');n.boolean(a.poll,'polling');
  const key=n.canonical([a.retry_group,a.destination,a.operation,a.authorization_key]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(a);
 }
 const policy=d.retry_policy;n.boolean(policy.idempotent,'operation retry safety');n.boolean(policy.jitter,'jitter policy');n.boolean(policy.backoff,'backoff policy');n.number(policy.max_attempts,'configured retry cap');
 const findings=[];
 for(const values of groups.values()){
  const a=[...values].sort((x,y)=>x.attempt-y.attempt);
  n.requireValue(a.every((v,i)=>v.attempt===i+1),'Missing or duplicate attempt sequence');
  const gaps=a.slice(1).map((v,i)=>v.start_ms-a[i].end_ms);n.requireValue(gaps.every(g=>g>=0),'Overlapping attempts');
  if(a.length<ctx.min_retry_attempts||a.some(v=>v.poll)||a.slice(1).some(v=>!v.client_retry)||a.slice(0,-1).some(v=>v.status<429))continue;
  const ignoredRetryAfter=a.slice(1).some((v,i)=>a[i].retry_after_ms!==undefined&&gaps[i]<n.number(a[i].retry_after_ms,'Retry-After'));
  const rapid=gaps.every(g=>g<ctx.min_retry_delay_ms);
  if(!ignoredRetryAfter&&!(rapid&&!policy.backoff&&!policy.jitter)&&!(policy.max_attempts>0&&a.length>policy.max_attempts))continue;
  const first=a[0];findings.push(n.finding(d,ctx,source,['attempts','retry_policy','trace'],first.destination+':'+first.operation+':retry-policy','Correlated failing client retries violate the supplied retry delay/cap or Retry-After policy.',policy.idempotent?'Use capped exponential backoff with jitter and honor Retry-After; retain deadlines and idempotency guarantees.':'Review operation idempotency before retries; do not automatically retry side effects. Apply bounded delays and Retry-After only after safety review.',[n.references.retry]));
 }
 return [...new Map(findings.map(f=>[f.identity,f])).values()];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-05',inspect),inspect};
