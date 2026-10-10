'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process');
const {network}=require('../core/dispatch'),{validatePair,compare}=require('../core/contract');
for(const [check,detector] of Object.entries(network)){
 test(check+' committed six-case behavior, Python pair validation and tampered citation rejection',()=>{
  const pairs=[];
  for(let i=1;i<=6;i++){
   const name=check.toLowerCase()+'-'+String(i).padStart(2,'0'),dir=path.join(__dirname,'fixtures/network');
   const input=JSON.parse(fs.readFileSync(path.join(dir,name+'-input.json'),'utf8')),expected=JSON.parse(fs.readFileSync(path.join(dir,name+'-result.json'),'utf8'));
   const result=detector.evaluate(input);assert.deepEqual(result,expected);validatePair(input,result);assert.deepEqual(result.measurements,[]);pairs.push({input,result});
   if(i===1){assert.equal(result.findings.length,1);assert.equal(result.status,'completed');const tampered=JSON.parse(JSON.stringify(result));tampered.findings[0].evidence[0].value=null;assert.throws(()=>validatePair(input,tampered));}
   if(i===2||i===3){assert.equal(result.findings.length,0);assert.equal(result.status,'completed');}
   if(i===4||i===5)assert.equal(result.status,'unavailable');
  }
  const py=spawnSync(process.env.G01_PYTHON||'python',['-c','import json,sys; from shared.contracts.validation import validate_pair; [validate_pair(p["input"],p["result"]) for p in json.load(sys.stdin)]'],{input:JSON.stringify(pairs),encoding:'utf8'});assert.equal(py.status,0,py.stderr);
  assert.equal(compare(pairs[0].result,pairs[3].result).no_longer_detected.length,0);
 });
}
