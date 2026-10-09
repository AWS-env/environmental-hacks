'use strict';
const crypto=require('node:crypto');
const {S3Client,GetObjectCommand,PutObjectCommand}=require('@aws-sdk/client-s3');
const {EventBridgeClient,PutEventsCommand}=require('@aws-sdk/client-eventbridge');
const {evaluate}=require('../core/g01');
const {validate,validatePair,canonical}=require('../core/contract');
const {collect}=require('../collectors/query-logs');
const config={region:process.env.AWS_REGION||'eu-north-1',maxAttempts:2};
const s3=new S3Client(config),bus=new EventBridgeClient(config);
const MAX_INPUT=1024*1024;
function sha(text){return crypto.createHash('sha256').update(text).digest('hex');}
/** @param {any} event @param {{send:Function}} client @param {string} bucket */
async function resolveInput(event,client,bucket) {
  if(event?.input?.kind==='input')return JSON.parse(canonical(event.input));
  if(event?.kind==='input')return JSON.parse(canonical(event));
  if(!event || typeof event.input_key!=='string' || !/^inputs\/[a-zA-Z0-9/_-]+\.json$/.test(event.input_key)||!/^([a-f0-9]{64})$/.test(event.sha256||''))throw new Error('Require contract input or authorized inputs key and SHA256');
  const response=await client.send(new GetObjectCommand({Bucket:bucket,Key:event.input_key}));
  if(!response.Body || response.ContentLength>MAX_INPUT)throw new Error('Input object missing or too large');
  let total=0;const chunks=[];
  for await(const chunk of response.Body) {total+=chunk.length;if(total>MAX_INPUT)throw new Error('Input stream exceeds bound');chunks.push(chunk);}
  const text=Buffer.concat(chunks).toString('utf8');
  if(sha(text)!==event.sha256)throw new Error('Input object checksum mismatch');
  return JSON.parse(text);
}
/** @param {'static'|'log'} family @param {any} event @param {any} invocation
 * @param {{s3?:{send:Function},bus?:{send:Function},collect?:Function,bucket?:string,hub?:string,groups?:string[]}} [deps] */
async function processInput(family,event,invocation,deps={}) {
  const storage=deps.s3||s3,publisher=deps.bus||bus,bucket=deps.bucket||process.env.ARTIFACT_BUCKET;
  if(!bucket)throw new Error('Artifact bucket not configured');
  if(Buffer.byteLength(canonical(event))>MAX_INPUT)throw new Error('Invocation input exceeds bound');
  const input=await resolveInput(event,storage,bucket);
  validate(input);
  if(input.sources.length>40||input.scope.length>20)throw new Error('Too many scope/source items');
  if(family==='log' && !['DB-05','DB-16'].includes(input.check_id))throw new Error('LOG handles DB-05/DB-16 only');
  if(family==='log' && event.log_window) {
    // LOG acquisition owns telemetry provenance; externally submitted telemetry is not substituted for provider reads.
    if(input.sources.some(s=>s.kind==='telemetry'))throw new Error('LOG acquisition requires static sources only');
    const acquire=deps.collect||collect,groups=deps.groups||JSON.parse(process.env.QUERY_LOG_GROUPS||'[]');
    for(const source of input.sources.filter(s=>s.kind==='static')) {
      let data;
      try {data=await acquire(event.log_window,groups,{repository_id:input.repository_id,commit_sha:input.commit_sha,locator:source.locator,source_sha256:sha(source.content)});}
      catch(e){data={format:'query-event-v1',acquisition:{status:'unavailable',reason:e.message},events:[]};}
      input.sources.push({source_id:'cloudwatch-'+sha(source.source_id).slice(0,16),scope_id:source.scope_id,kind:'telemetry',locator:'cloudwatch:'+event.log_window.group,data});
    }
  }
  const result=evaluate(input);
  validatePair(input,result);
  const text=canonical({input,result}),digest=sha(text);
  const key='results/'+sha(canonical([input.repository_id,input.scan_id,input.check_id,input.detector_version])).slice(0,32)+'/'+digest+'.json';
  // Content-addressed immutable pair: duplicate invocations converge to one artifact, never overwrite a differing result.
  let stored;
  try {stored=await storage.send(new PutObjectCommand({Bucket:bucket,Key:key,Body:text,ContentType:'application/json',IfNoneMatch:'*'}));}
  catch(e){if(e.name!=='PreconditionFailed'&&e.$metadata?.httpStatusCode!==412)throw e;stored={};}
  const artifact={bucket,key,sha256:digest,...(stored.VersionId?{version_id:stored.VersionId}:{})};
  const hub=deps.hub??process.env.FINDINGS_HUB_ARN;
  let delivery={status:'blocked',reason:'Owner D findings hub is not configured; S3 artifact is not report persistence'};
  if(hub) {
    const response=await publisher.send(new PutEventsCommand({Entries:[{EventBusName:hub,Source:'owner-b.detectors',DetailType:'DetectorResultPointer.v1',Detail:canonical({repository_id:input.repository_id,scan_id:input.scan_id,check_id:input.check_id,detector_version:input.detector_version,commit_sha:input.commit_sha,artifact})}]}));
    if(response.FailedEntryCount || !response.Entries?.[0]?.EventId)throw new Error('EventBridge entry failed: '+(response.Entries?.[0]?.ErrorCode||'Missing EventId'));
    delivery={status:'published',reason:'Event accepted; Owner D writer readback is still required'};
  }
  return {result,artifact,delivery,request_id:invocation?.awsRequestId||'local-unit-test'};
}
module.exports={processInput,resolveInput};
