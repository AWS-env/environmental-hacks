'use strict';
const crypto=require('node:crypto');
const {S3Client,PutObjectCommand}=require('@aws-sdk/client-s3');
const {EventBridgeClient,PutEventsCommand}=require('@aws-sdk/client-eventbridge');
const {resolveInput}=require('./pipeline');
const {validate,validatePair,canonical}=require('../core/contract');
const {evaluate}=require('../core/dispatch');
const provider=require('../collectors/job-provider');
const config={region:process.env.AWS_REGION||'ap-south-1',maxAttempts:2};
const storage=new S3Client(config),publisher=new EventBridgeClient(config);
const families={static:['JOB-04','JOB-01','DB-13','DB-39'],log:['JOB-05'],telemetry:['JOB-03','JOB-01'],heuristic:['JOB-06']};
const hash=text=>crypto.createHash('sha256').update(text).digest('hex');
async function processJobs(family,event,context,deps={}){
  const s3=deps.s3||storage,bus=deps.bus||publisher,bucket=deps.bucket||process.env.ARTIFACT_BUCKET;
  if(!bucket||Buffer.byteLength(canonical(event))>1048576)throw new Error('Missing bucket or oversized input');
  const input=await resolveInput(event,s3,bucket);validate(input);
  if(!families[family]?.includes(input.check_id)||input.scope.length>20||input.sources.length>40)throw new Error('Unsupported family/check or input count');
  if(event.acquisition){
    if(input.scope.length!==1||input.sources.some(s=>s.kind==='telemetry'))throw new Error('Provider acquisition requires one scope and no prefilled telemetry');
    const a=event.acquisition,p=deps.provider||provider;let data;
    try{
      const snapshot={repository_id:input.repository_id,commit_sha:input.commit_sha};
      if(input.check_id==='JOB-03'){
        const setup=JSON.parse(process.env.WORKER_POOLS||'{}')[a.pool_id];if(!setup)throw new Error('Worker pool not authorized/configured');
        data=await p.collectMetrics(a.window,setup.queries,{...snapshot,pool_id:a.pool_id,policy:setup.policy});
      }else if(input.check_id==='JOB-05'){
        const setup=JSON.parse(process.env.JOB_LOG_SOURCES||'{}')[a.job_id];if(!setup)throw new Error('Job source not authorized/configured');
        data=await p.collectJobLogs(a.window,[setup.group],{...snapshot,job_id:a.job_id});data.policy=setup.policy;
      }else if(input.check_id==='JOB-04'){
        const setup=JSON.parse(process.env.SCHEDULE_GROUPS||'{}')[a.group];if(!setup)throw new Error('Schedule group not authorized/configured');
        data=await p.collectSchedules(a.group,[a.group],setup);
      }else throw new Error('Request-span acquisition requires a connector-produced existing trace artifact; no synthetic provider substituted');
    }catch(e){data={format:input.check_id==='JOB-03'?'worker-capacity-v1':input.check_id==='JOB-05'?'job-runs-v1':'schedule-inventory-v1',acquisition:{status:'unavailable',reason:e.message}};}
    input.sources.push({source_id:'provider-'+hash(canonical(a)).slice(0,16),scope_id:input.scope[0],kind:input.check_id==='JOB-04'?'artifact':'telemetry',locator:'aws:job-evidence',data});
  }
  const result=await evaluate(input);validatePair(input,result);
  const body=canonical({input,result}),digest=hash(body),key='results/'+(input.check_id.startsWith('JOB-')?'jobs':'orm')+'/'+hash(canonical([input.repository_id,input.scan_id,input.check_id,input.detector_version])).slice(0,32)+'/'+digest+'.json';
  let saved;try{saved=await s3.send(new PutObjectCommand({Bucket:bucket,Key:key,Body:body,ContentType:'application/json',IfNoneMatch:'*'}));}catch(e){if(e.name!=='PreconditionFailed'&&e.$metadata?.httpStatusCode!==412)throw e;saved={};}
  const artifact={bucket,key,sha256:digest,...(saved.VersionId?{version_id:saved.VersionId}:{})};
  const hub=deps.hub??process.env.FINDINGS_HUB_ARN;
  let delivery={status:'blocked',reason:'Owner D hub not configured'};
  if(hub){
    const response=await bus.send(new PutEventsCommand({Entries:[{EventBusName:hub,Source:'owner-b.detectors',DetailType:'DetectorResultPointer.v1',Detail:canonical({repository_id:input.repository_id,scan_id:input.scan_id,commit_sha:input.commit_sha,check_id:input.check_id,detector_version:input.detector_version,artifact})}]}));
    if(response.FailedEntryCount||!response.Entries?.[0]?.EventId)throw new Error('Hub publication failed');
    delivery={status:'published',reason:'Event accepted only; ingestion and persisted report readback must be verified'};
  }
  return {result,artifact,delivery,request_id:context?.awsRequestId||'local-unit-test'};
}
module.exports={processJobs};
