'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {validatePair,canonical}=require('../core/contract'),{evaluate}=require('../core/dispatch');
test('Public job evidence is reproducible and contains no concrete AWS identity/resource details',()=>{
  const root=path.join(__dirname,'../../../docs/verification/owner-b/jobs');let pairs=0;
  function privacy(directory){for(const name of fs.readdirSync(directory)){const file=path.join(directory,name);if(fs.statSync(file).isDirectory())privacy(file);else if(/\.(json|md)$/.test(name))assert.doesNotMatch(fs.readFileSync(file,'utf8'),/\barn:aws(?:-[a-z]+)?:|\bAIDA[A-Z0-9]{16}\b|\b\d{12}\b|"(?:profile|UserId|Account|StackId|FunctionArn)"/);}}
  privacy(root);
  for(const key of ['job-01','job-03','job-04','job-05','job-06'])for(const name of ['positive','negative','unavailable']){
    const text=fs.readFileSync(path.join(root,key,name+'.json'),'utf8');assert.doesNotMatch(text,/\barn:aws(?:-[a-z]+)?:|\bAIDA[A-Z0-9]{16}\b|\b\d{12}\b|"(?:profile|UserId|Account|StackId|FunctionArn)"/);
    const pair=JSON.parse(text);validatePair(pair.input,pair.result);assert.equal(canonical(evaluate(pair.input)),canonical(pair.result));assert.equal(pair.input.repository_id,'synthetic:job-fixtures');pairs++;
  }
  assert.equal(pairs,15);
});
