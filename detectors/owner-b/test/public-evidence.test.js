'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {validatePair,canonical}=require('../core/contract');
const {evaluate}=require('../core/g01');
const directory=path.join(__dirname,'../../../docs/verification/owner-b');
function files(dir) {return fs.readdirSync(dir,{withFileTypes:true}).flatMap(entry=>entry.isDirectory()?files(path.join(dir,entry.name)):[path.join(dir,entry.name)]);}
test('Public evidence excludes AWS identity and concrete resource metadata',()=>{
  for(const file of files(directory).filter(name=>name.endsWith('.json'))) {
    const text=fs.readFileSync(file,'utf8');
    assert.doesNotMatch(text,/arn:aws:[^"\s]*:\d{12}:/,file);
    assert.doesNotMatch(text,/(?:AIDA|AROA)[A-Z0-9]{12,}/,file);
    assert.doesNotMatch(text,/"(?:Account|UserId|profile|bucket|artifact_bucket|StackName)"\s*:\s*"(?!<)[^"]+"/,file);
    assert.doesNotMatch(text,/"working_tree_uncommitted"/,file);
  }
});
test('Sanitized public pairs retain valid contracts, local results and separately labelled hashes',()=>{
  const dbDirectories=['db-34','db-06','db-05','db-16'];
  for(const file of dbDirectories.filter(name=>fs.existsSync(path.join(directory,name))).flatMap(name=>files(path.join(directory,name))).filter(name=>name.endsWith('receipts.json'))) {
    const receipt=JSON.parse(fs.readFileSync(file,'utf8'));
    assert.equal(receipt.public_redaction.byte_identical_to_s3,false);
    for(const record of receipt.records) {
      validatePair(record.input,record.result);
      assert.deepEqual(evaluate(record.input),record.result);
      assert.equal(record.public_pair_sha256,crypto.createHash('sha256').update(canonical({input:record.input,result:record.result})).digest('hex'));
      assert.equal(record.artifact.hash_kind,'original_private_pair_sha256');
    }
  }
});
