'use strict';
const crypto=require('node:crypto'),os=require('node:os'),fs=require('node:fs'),{readJson}=require('./artifact-json'),n=require('../core/network'),{validate}=require('../core/contract');
const hash=x=>crypto.createHash('sha256').update(n.canonical(x)).digest('hex');
/** Benchmark only a supplied JSON array of finite numbers using this auditor's own codecs.
 * @param {any} input @param {any[]} values @param {any} options */
function captureNumeric(input,values,options){
 validate(input);n.requireValue(['NET-08','NET-10'].includes(input.check_id)&&input.scope.length===1,'One numeric comparison scope required');
 n.requireValue(Array.isArray(values)&&values.length>0&&values.length<=100000&&values.every(v=>typeof v==='number'&&Number.isFinite(v)&&!Object.is(v,-0)),'Require bounded finite numeric array; JSON negative-zero equivalence is unsupported');
 n.requireValue(['client','auditor'].includes(options.origin),'Explicit workload origin');n.requireValue(Number.isInteger(options.operations)&&options.operations>0,'Observed workload frequency required');
 const repetitions=20,iterations=50,warmups=5,start=new Date().toISOString(),inputDigest=hash(values),schemaDigest=hash('ordered finite IEEE754 float64 array');
 const candidates=[
 {format:'json',binary:false,encode:v=>Buffer.from(JSON.stringify(v)),decode:b=>JSON.parse(b.toString('utf8'))},
 {format:'float64-le',binary:true,encode:v=>{const b=Buffer.allocUnsafe(v.length*8);for(let i=0;i<v.length;i++)b.writeDoubleLE(v[i],i*8);return b;},decode:b=>{const a=[];for(let i=0;i<b.length;i+=8)a.push(b.readDoubleLE(i));return a;}}
 ];
 const runs=[];
 for(const codec of candidates){
  const bytes=codec.encode(values),decoded=codec.decode(bytes);n.requireValue(hash(decoded)===inputDigest,'Codec roundtrip mismatch');
  for(let i=0;i<warmups;i++)codec.decode(codec.encode(values));
  const samples=[];
  for(let i=0;i<repetitions;i++){
   let before=process.cpuUsage();for(let k=0;k<iterations;k++)codec.encode(values);let usage=process.cpuUsage(before),encode_ms=(usage.user+usage.system)/1000/iterations;
   before=process.cpuUsage();for(let k=0;k<iterations;k++)codec.decode(bytes);usage=process.cpuUsage(before);const decode_ms=(usage.user+usage.system)/1000/iterations;
   samples.push({bytes:bytes.length,encode_ms,decode_ms});
  }
  runs.push({input_digest:inputDigest,output_digest:inputDigest,schema_digest:schemaDigest,format:codec.format,binary:codec.binary,runtime:process.version,machine:hash([os.platform(),os.arch(),os.cpus()[0]?.model]),tool_version:'owner-b-numeric-1',warmup_policy:'5 unmeasured; 50 codec operations/sample',compression:'none',timing_kind:'cpu',time_unit:'milliseconds',size_unit:'bytes',samples});
 }
 const end=new Date().toISOString(),benchmark={format_version:1,input_digest:inputDigest,schema_digest:schemaDigest,workload:options.workload,operations:options.operations,hot_path:options.hot_path,interoperability_allows_change:options.interoperability_allows_change,equivalence:{verified:true,unknown_fields_preserved:true,precision_preserved:true,output_digest:inputDigest},baseline:runs[0],candidate:runs[1]};
 const data={format:'network-evidence-v1',repository_id:input.repository_id,commit_sha:input.commit_sha,route:options.route,capture:{tool:'owner-b-numeric-export',version:'1',origin:options.origin},acquisition:{status:'complete',start,end},benchmark,numeric_array:{elements:values.length,distribution_digest:inputDigest,precision:'IEEE754 float64 exact; finite, no negative zero',input_digest:inputDigest,lossless:true}};
 const output={...input,sources:[{source_id:'numeric-capture',scope_id:input.scope[0],kind:'artifact',locator:'artifact:client-numeric-comparison',data}]};validate(output);return output;
}
if(require.main===module){const [inputFile,valuesFile,optionsFile,outputFile]=process.argv.slice(2);n.requireValue(outputFile,'Usage: network-benchmark.js input.json numeric-array.json options.json output.json');fs.writeFileSync(outputFile,JSON.stringify(captureNumeric(readJson(inputFile),readJson(valuesFile),readJson(optionsFile)),null,2)+'\n');}
module.exports={captureNumeric};
