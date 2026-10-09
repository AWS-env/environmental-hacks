'use strict';
const crypto=require('node:crypto');
const {S3Client,GetObjectCommand,PutObjectCommand}=require('@aws-sdk/client-s3');
const {EventBridgeClient,PutEventsCommand}=require('@aws-sdk/client-eventbridge');
const c=require('../core/contract'),n=require('../core/network'),provider=require('../collectors/network-provider');
const config={region:process.env.AWS_REGION||'ap-south-1',maxAttempts:2},storage=new S3Client(config),publisher=new EventBridgeClient(config);
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
const AbortSignal=globalThis.AbortSignal;
/** @param {any} event @param {any} s3 @param {string} bucket */
async function loadInput(event,s3,bucket){
 if(event.kind==='input')return JSON.parse(c.canonical(event));
 if(event.input?.kind==='input')return JSON.parse(c.canonical(event.input));
 n.requireValue(/^inputs\/network\/[a-f0-9]{32}\/[a-f0-9]{64}\.json$/.test(event.input_key||''),'Invalid network artifact key');
 n.requireValue(typeof event.version_id==='string'&&event.version_id.length>0,'Immutable input version required');
 n.requireValue(/^[a-f0-9]{64}$/.test(event.sha256||'')&&event.input_key.endsWith('/'+event.sha256+'.json'),'Checksum/key mismatch');
 const response=await s3.send(new GetObjectCommand({Bucket:bucket,Key:event.input_key,VersionId:event.version_id}),{abortSignal:AbortSignal.timeout(15000)});
 const chunks=[];let size=0;
 try{
  n.requireValue(response.Body&&response.ContentLength<=1048576,'Oversized/missing artifact');
  for await(const chunk of response.Body){size+=chunk.length;n.requireValue(size<=1048576,'Artifact stream bound');chunks.push(chunk);}
 }finally{response.Body?.destroy?.();}
 const body=Buffer.concat(chunks);n.requireValue(hash(body)===event.sha256,'Artifact checksum mismatch');
 return JSON.parse(body.toString('utf8'));
}
/** @param {any} event @param {any} context @param {any} [deps] */
async function processNetwork(event,context,deps={}){
 n.requireValue(Buffer.byteLength(c.canonical(event))<=1048576,'Invocation input bound');
 const s3=deps.s3||storage,bus=deps.bus||publisher,bucket=deps.bucket||process.env.ARTIFACT_BUCKET;
 n.text(bucket,'artifact bucket');
 if(event.Records){
  n.requireValue(Array.isArray(event.Records)&&event.Records.length<=10,'S3 event count bound');
  const outputs=[];
  for(const record of event.Records){
   n.requireValue(record.eventSource==='aws:s3'&&record.s3.bucket.name===bucket&&record.eventName.startsWith('ObjectCreated:'),'Unauthorized S3 event');
   const key=decodeURIComponent(record.s3.object.key.replace(/\+/g,' '));
   outputs.push(await processNetwork({input_key:key,version_id:record.s3.object.versionId,sha256:key.split('/').at(-1).replace(/\.json$/,'')},context,deps));
  }
  return outputs;
 }
 const input=await loadInput(event,s3,bucket);c.validate(input);
 n.requireValue(Object.hasOwn(require('../core/dispatch').network,input.check_id),'Unsupported network check');
 n.requireValue(input.scope.length<=20&&input.sources.length<=40,'Input scope/source bound');
 if(event.acquisition){
  n.requireValue(input.scope.length===1&&!input.sources.some(s=>s.kind!=='static'),'Provider acquisition rejects prefilled evidence');
  const a=event.acquisition,setup=(deps.setups||JSON.parse(process.env.NETWORK_SOURCES||'{}'))[a.source_id];n.requireValue(setup&&setup.repository_id===input.repository_id&&setup.route===a.route,'Provider source not authorized');
  let data;
  try{
   const snapshot={repository_id:input.repository_id,commit_sha:input.commit_sha,route:setup.route};
   const p=deps.provider||provider;
   n.requireValue(['xray','metrics','logs'].includes(setup.kind),'Unsupported provider source');
   data=setup.kind==='xray'?await p.collectTraces(a.window,setup,snapshot):setup.kind==='logs'?await p.collectLogs(a.window,setup,snapshot):await p.collectMetrics(a.window,setup,snapshot);
  }catch(e){data={format:'network-evidence-v1',acquisition:{status:'unavailable',reason:e.message}};}
  input.sources.push({source_id:'provider-capture',scope_id:input.scope[0],kind:'telemetry',locator:'aws:authorized-network-source',data});
 }
 const result=require('../core/dispatch').evaluate(input);c.validatePair(input,result);
 const body=c.canonical({input,result}),sha256=hash(body),key='results/network/'+hash(c.canonical([input.repository_id,input.scan_id,input.check_id,input.detector_version])).slice(0,32)+'/'+sha256+'.json';
 let saved;try{saved=await s3.send(new PutObjectCommand({Bucket:bucket,Key:key,Body:body,ContentType:'application/json',IfNoneMatch:'*'}));}catch(e){if(e.name!=='PreconditionFailed'&&e.$metadata?.httpStatusCode!==412)throw e;saved={};}
 const artifact={bucket,key,sha256,...(saved.VersionId?{version_id:saved.VersionId}:{})},hub=deps.hub??process.env.FINDINGS_HUB_ARN;
 n.text(hub,'findings hub');
 const published=await bus.send(new PutEventsCommand({Entries:[{EventBusName:hub,Source:'owner-b.detectors',DetailType:'DetectorResultPointer.v1',Detail:c.canonical({...Object.fromEntries(['repository_id','scan_id','commit_sha','check_id','detector_version'].map(k=>[k,input[k]])),artifact})}]}));
 n.requireValue(!published.FailedEntryCount&&published.Entries?.[0]?.EventId,'Hub event publication failed');
 return {result,artifact,delivery:{status:'published',reason:'Accepted event; report readback still required'},request_id:context?.awsRequestId||'unit-test'};
}
module.exports={handler:processNetwork,processNetwork,loadInput};
