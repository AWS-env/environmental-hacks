'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {collect}=require('../collectors/query-logs');
const {processInput}=require('../handlers/pipeline');
const {input,withEvents}=require('./g01-fixtures');
test('AWS unit: query acquisition polls, records query receipts and deduplicates',async()=>{
  const e=withEvents(input('DB-05')).sources[1].data.events[0];
  const calls=[];
  const client={async send(cmd){calls.push(cmd.constructor.name);if(cmd.constructor.name==='StartQueryCommand')return{queryId:'unit-query'};return{status:'Complete',statistics:{recordsMatched:1},results:[[{field:'@message',value:JSON.stringify({format:'query-event-v1',event:e})}]]};}};
  const data=await collect({group:'/unit',start:'2026-10-09T00:00:00Z',end:'2026-10-09T00:01:00Z'},['/unit'],{repository_id:e.repository_id,commit_sha:e.commit_sha,locator:e.locator,source_sha256:e.source_sha256},client,async()=>{});
  assert.equal(data.events.length,1);assert.equal(data.acquisition.receipts[0].query_id,'unit-query');assert.deepEqual(calls,['StartQueryCommand','GetQueryResultsCommand']);
});
test('AWS unit: denied, failed, truncated and timed-out queries never certify acquisition',async()=>{
  const w={group:'/unit',start:'2026-10-09T00:00:00Z',end:'2026-10-09T00:00:01Z'},s={repository_id:'repo',commit_sha:'a'.repeat(40),locator:'x',source_sha256:'a'.repeat(64)};
  const client={async send(){throw new Error('AccessDenied');}};
  await assert.rejects(()=>collect(w,['/unit'],s,client),/AccessDenied/);
  await assert.rejects(()=>collect(w,[],s,client),/authorized/);
  for(const response of [{status:'Failed'},{status:'Complete',statistics:{recordsMatched:1001},results:[]},{status:'Complete',results:[[{field:'@message',value:'bad json'}]]}]) {
    const c={async send(cmd){return cmd.constructor.name==='StartQueryCommand'?{queryId:'q'}:response;}};
    await assert.rejects(()=>collect(w,['/unit'],s,c,async()=>{}));
  }
  const calls=[];const c={async send(cmd){calls.push(cmd.constructor.name);return cmd.constructor.name==='StartQueryCommand'?{queryId:'q'}:{status:'Running'};}};
  await assert.rejects(()=>collect(w,['/unit'],s,c,async()=>{}),/deadline/);assert.equal(calls.at(-1),'StopQueryCommand');
});
test('AWS unit: validated immutable pair stored, missing hub stays explicitly blocked',async()=>{
  const commands=[];const s3={async send(c){commands.push(c);return{VersionId:'unit-version'};}};
  const p=input('DB-34');
  const a=await processInput('static',p,{awsRequestId:'unit'},{bucket:'unit',s3,hub:''});
  const b=await processInput('static',p,{awsRequestId:'unit-again'},{bucket:'unit',s3,hub:''});
  assert.equal(a.artifact.key,b.artifact.key);assert.equal(a.delivery.status,'blocked');assert.equal(a.result.findings.length,1);
  assert.equal(commands[0].input.IfNoneMatch,'*');assert.ok(JSON.parse(commands[0].input.Body).input.sources[0].content);
});
test('AWS unit: storage denial and per-entry EventBridge failure raise; duplicate storage is idempotent',async()=>{
  await assert.rejects(()=>processInput('static',input('DB-34'),{}, {bucket:'unit',s3:{async send(){throw new Error('AccessDenied');}}}),/AccessDenied/);
  const s3={async send(){const e=new Error('already stored');e.name='PreconditionFailed';throw e;}};
  const bus={async send(){return{FailedEntryCount:1,Entries:[{ErrorCode:'AccessDeniedException'}]};}};
  await assert.rejects(()=>processInput('static',input('DB-34'),{},{bucket:'unit',hub:'unit-bus',s3,bus}),/EventBridge entry failed/);
  const result=await processInput('static',input('DB-34'),{},{bucket:'unit',hub:'',s3});assert.equal(result.delivery.status,'blocked');
});
test('AWS unit: real-collector failure produces unavailable hybrid result, not a clean report',async()=>{
  const event={input:input('DB-05'),log_window:{group:'/unit',start:'2026-10-09T00:00:00Z',end:'2026-10-09T00:01:00Z'}};
  const receipt=await processInput('log',event,{},{bucket:'unit',hub:'',s3:{async send(){return{};}},collect:async()=>{throw new Error('AccessDenied');}});
  assert.equal(receipt.result.status,'unavailable');assert.match(receipt.result.coverage.limitations.join(' '),/query-event-v1/);
});
