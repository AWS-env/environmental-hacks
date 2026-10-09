'use strict';
const {CronExpressionParser}=require('cron-parser');
const j=require('../core/jobs');
const REF='https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html';
// AWS cron weekday numbering differs from POSIX. Only the explicitly supported subset is translated.
function expression(job){
  if(job.dialect==='posix'){
    j.requireValue(job.expression.trim().split(/\s+/).length===5,'Require POSIX five-field cron');
    j.requireValue(/^[\d\s*,/\-]+$/.test(job.expression),'Unsupported POSIX cron modifier');return job.expression;
  }
  j.requireValue(job.dialect==='aws-scheduler','Unsupported schedule dialect');
  const m=/^cron\(([^)]+)\)$/.exec(job.expression);j.requireValue(m,'Not AWS cron');
  const f=m[1].trim().split(/\s+/);j.requireValue(f.length===6&&f[5]==='*','AWS year constraints not supported');
  j.requireValue((f[2]==='?')!==(f[4]==='?'),'Exactly one AWS day field must be ?');
  j.requireValue(f.slice(0,4).every(x=>/^[\d*,/\-?]+$/.test(x)),'Unsupported AWS cron modifier');
  let dow='*';if(f[4]!=='?'){
    j.requireValue(/^[1-7]$/.test(f[4]),'Only single numeric AWS weekday supported');dow=String(Number(f[4])-1);
  }
  return [f[0],f[1],f[2]==='?'?'*':f[2],f[3],dow].join(' ');
}
function occurrences(job,start,end,limit){
  j.text(job.timezone,'timezone');new Intl.DateTimeFormat('en',{timeZone:job.timezone}).format(0);
  const rate=/^rate\(([1-9]\d*) (minute|minutes|hour|hours|day|days)\)$/.exec(job.expression);
  if(rate){
    j.requireValue(job.dialect==='aws-scheduler','Rate requires AWS dialect');
    const count=Number(rate[1]);j.requireValue((count===1)===!rate[2].endsWith('s'),'Incorrect AWS rate plural');
    const step=count*(rate[2].startsWith('minute')?60000:rate[2].startsWith('hour')?3600000:86400000);
    const anchor=j.instant(job.start);const values=[];
    for(let t=anchor+Math.max(0,Math.ceil((start-anchor)/step))*step;t<end;t+=step){values.push(t);j.requireValue(values.length<=limit,'Occurrence limit exceeded');}return values;
  }
  const expr=expression(job),clock=new Intl.DateTimeFormat('sv-SE',{timeZone:job.timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const parsed=CronExpressionParser.parse(expr,{currentDate:new Date(start-1),endDate:new Date(end-1),tz:job.timezone});
  const values=[],seen=new Set();
  while(parsed.hasNext()){
    const t=parsed.next().getTime(),local=clock.format(t);
    // cron-parser can roll a nonexistent spring-forward time forward. Reject that occurrence for AWS.
    if(job.dialect==='aws-scheduler'){
      const fields=expr.split(' '),parts=clock.formatToParts(t);const hour=Number(parts.find(p=>p.type==='hour').value),minute=Number(parts.find(p=>p.type==='minute').value);
      if(/^\d+$/.test(fields[1])&&hour!==Number(fields[1]))continue;
      if(/^\d+$/.test(fields[0])&&minute!==Number(fields[0]))continue;
      if(seen.has(local))continue;seen.add(local);
    }
    values.push(t);j.requireValue(values.length<=limit,'Occurrence limit exceeded');
  }
  return values;
}
function normalize(source,context){
  let data;if(source.kind==='static'){j.requireValue(Buffer.byteLength(source.content)<=262144,'Schedule manifest too large');data=JSON.parse(source.content);}else data=source.data;
  j.requireValue(data.format==='schedule-inventory-v1','Require schedule-inventory-v1 manifest');
  j.requireValue(data.complete===true,'Schedule inventory incomplete');j.unique(data.jobs,'job_id');j.requireValue(data.jobs.length<=50,'Too many schedules');
  const {start,end}=j.windowBounds(context.horizon);const limit=j.number(context.max_occurrences,'max_occurrences',1,2000);
  j.requireValue(Number.isInteger(limit),'Occurrence limit must be integer');
  const jobs=[];
  for(const job of data.jobs){
    j.boolean(job.enabled,'enabled');if(!job.enabled)continue;
    j.text(job.pool_id,'pool_id');j.text(job.expression,'expression');
    j.boolean(job.intentional_alignment,'intentional_alignment');j.boolean(job.serialized,'serialized');
    j.requireValue(job.flexible_window_seconds===0&&job.jitter_seconds===0,'Flexible/jittered execution timing cannot certify collisions');
    const capacity=j.number(job.pool_capacity,'pool_capacity',1,10000),slots=j.number(job.slots,'slots',1,capacity);
    let duration=null;if(job.duration_seconds!==undefined)duration=j.number(job.duration_seconds,'duration_seconds',1,86400);
    if(job.dialect==='aws-scheduler'&&!/^rate/.test(job.expression)){
      const f=/^cron\(([^)]+)\)$/.exec(job.expression)?.[1]?.split(/\s+/);
      j.requireValue(f&&f.slice(0,2).every(x=>x==='*'||/^\d+$/.test(x)),'AWS DST support limited to literal/wildcard minute/hour');
    }
    const lower=job.start?Math.max(start-(duration??60)*1000,j.instant(job.start)):start-(duration??60)*1000;
    const upper=job.end?Math.min(end,j.instant(job.end)):end;
    const times=upper>lower?occurrences(job,lower,upper,limit):[];
    jobs.push({...job,capacity,slots,duration,times});
  }
  return {jobs,start,end,data};
}
module.exports={normalize,occurrences,REF};
