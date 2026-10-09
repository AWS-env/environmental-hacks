'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),{Readable}=require('node:stream');
const {loadInput,processNetwork}=require('../handlers/network'),{handler:upload}=require('../handlers/network-upload'),provider=require('../collectors/network-provider');
const f=require('./network-fixtures');
test('Network handler validates pair before publishing and checks per-entry errors',async()=>{
 const saved=[],s3={send:async cmd=>{saved.push(cmd.input);return {VersionId:'v1'};}},bus={send:async()=>({FailedEntryCount:0,Entries:[{EventId:'fixture'}]})};
 const input=f.input('NET-01'),out=await processNetwork(input,{}, {s3,bus,bucket:'fixture-bucket',hub:'fixture-hub'});
 assert.equal(out.result.findings.length,1);assert.equal(saved[0].IfNoneMatch,'*');assert(saved[0].Key.startsWith('results/network/'));
 await assert.rejects(processNetwork(input,{}, {s3,bus:{send:async()=>({FailedEntryCount:1,Entries:[{ErrorCode:'Denied'}]})},bucket:'fixture-bucket',hub:'fixture-hub'}),/publication/);
});
test('Immutable artifact checksum, version and event bucket are enforced',async()=>{
 const body=Buffer.from(JSON.stringify(f.input('NET-01'))),sha=crypto.createHash('sha256').update(body).digest('hex'),key='inputs/network/'+'a'.repeat(32)+'/'+sha+'.json';
 const client={send:async cmd=>{assert.equal(cmd.input.VersionId,'v1');return {ContentLength:body.length,Body:Readable.from([body])};}};
 assert.equal((await loadInput({input_key:key,version_id:'v1',sha256:sha},client,'fixture')).check_id,'NET-01');
 await assert.rejects(loadInput({input_key:key,sha256:sha},client,'fixture'),/version/);
 await assert.rejects(processNetwork({Records:[{eventSource:'aws:s3',eventName:'ObjectCreated:Put',s3:{bucket:{name:'wrong'},object:{key}}}]},{},{bucket:'fixture'}),/Unauthorized/);
});
test('Upload signer authorizes repository and signs checksum, conditional write and bounded size',async()=>{
 const deps={repositories:['fixture:owner-b/network'],bucket:'fixture',sign:async(_client,cmd,opts)=>{assert.equal(opts.expiresIn,300);assert.equal(cmd.input.IfNoneMatch,'*');assert.equal(cmd.input.ChecksumSHA256,Buffer.from('a'.repeat(64),'hex').toString('base64'));return 'fixture-signed-url';}};
 const r=await upload({repository_id:'fixture:owner-b/network',sha256:'a'.repeat(64),bytes:123},{},deps);assert(r.key.startsWith('inputs/network/'));assert.equal(r.headers['content-length'],'123');
 await assert.rejects(upload({repository_id:'other',sha256:'a'.repeat(64),bytes:123},{},deps),/authorized/);
});
test('Real SDK presigner binds every required upload header without hoisting the checksum',async()=>{
 const {S3Client}=require('@aws-sdk/client-s3');
 const s3=new S3Client({region:'ap-south-1',credentials:{accessKeyId:'fixture-access-key',secretAccessKey:'fixture-signing-key'}});
 try{
  const signed=await upload({repository_id:'fixture:owner-b/network',sha256:'a'.repeat(64),bytes:123},{},{repositories:['fixture:owner-b/network'],bucket:'fixture-private',s3});
  const query=new (require('node:url').URL)(signed.url).searchParams,headers=query.get('X-Amz-SignedHeaders').split(';');
  for(const header of Object.keys(signed.headers))assert(headers.includes(header),'Unsigned required header '+header);
  assert(!query.has('x-amz-checksum-sha256'));
 }finally{s3.destroy();}
});

test('X-Ray acquisition paginates summaries and rejects empty, unprocessed and malformed evidence',async()=>{
 const window={start:'2026-10-10T00:00:00Z',end:'2026-10-10T00:01:00Z'},snapshot={repository_id:'fixture',commit_sha:'a'.repeat(40),route:'r'};
 let calls=0;await assert.rejects(provider.collectTraces(window,{filter:'service("fixture")'},snapshot,{send:async()=>++calls===1?{TraceSummaries:[],NextToken:'next'}:{TraceSummaries:[]}}),/No matching/);assert.equal(calls,2);
 await assert.rejects(provider.collectTraces(window,{filter:'fixture'},snapshot,{send:async cmd=>cmd.constructor.name==='GetTraceSummariesCommand'?{TraceSummaries:[{Id:'t'}]}:{Traces:[],UnprocessedTraceIds:['t']}}),/Incomplete/);
});
test('CloudWatch empty samples and partial provider status never become zero readings',async()=>{
 const window={start:'2026-10-10T00:00:00Z',end:'2026-10-10T00:01:00Z'},setup={queries:[{Id:'origin_requests',MetricStat:{Metric:{Namespace:'OwnerB/Network',MetricName:'OriginRequests'},Period:60,Stat:'Sum',Unit:'Count'}}]};
 await assert.rejects(provider.collectMetrics(window,setup,{}, {send:async()=>({MetricDataResults:[{Id:'origin_requests',StatusCode:'Complete',Values:[],Timestamps:[]}]})}),/Missing metric/);
 await assert.rejects(provider.collectMetrics(window,setup,{}, {send:async()=>({MetricDataResults:[{Id:'origin_requests',StatusCode:'PartialData',Values:[],Timestamps:[]}]})}),/Incomplete/);
});

test('X-Ray normalization rejects other routes and preserves truncated capture status',()=>{
 const {normalizeTraces}=require('../normalization/xray');
 const snapshot={repository_id:'fixture:owner-b/network',commit_sha:'b'.repeat(40),route:'catalog'};
 const segment={id:'segment',start_time:1,end_time:2,metadata:{owner_b:{...snapshot,capture_origin:'synthetic'}}};
 const traces=[{Id:'trace',Segments:[{Document:JSON.stringify(segment)}]}];
 assert.equal(normalizeTraces(traces,snapshot,{status:'complete'}).trace.complete,true);
 traces[0].LimitExceeded=true;assert.equal(normalizeTraces(traces,snapshot,{status:'complete'}).trace.complete,false);
 segment.metadata.owner_b.route='checkout';traces[0].Segments[0].Document=JSON.stringify(segment);
 assert.throws(()=>normalizeTraces(traces,snapshot,{status:'complete'}),/route mismatch/);
});
test('Existing normalized log captures require identity, uniqueness and terminal query status',async()=>{
 const snapshot={repository_id:'fixture:owner-b/network',commit_sha:'b'.repeat(40),route:'catalog'},window={start:'2026-10-10T00:00:00Z',end:'2026-10-10T00:01:00Z'},setup={group:'fixture-log'};
 const message={format:'network-evidence-event-v1',...snapshot,data:f.evidence()};
 const data=await provider.collectLogs(window,setup,snapshot,{send:async cmd=>cmd.constructor.name==='StartQueryCommand'?{queryId:'q'}:{status:'Complete',results:[[{field:'@message',value:JSON.stringify(message)}]],statistics:{recordsMatched:1}}});assert.equal(data.acquisition.provider,'cloudwatch-logs');
 let stopped=false;await assert.rejects(provider.collectLogs(window,setup,snapshot,{send:async cmd=>{if(cmd.constructor.name==='StartQueryCommand')return {queryId:'q'};if(cmd.constructor.name==='StopQueryCommand'){stopped=true;return {};}return {status:'Running'};}},async()=>{}),/deadline/);assert(stopped);
});
