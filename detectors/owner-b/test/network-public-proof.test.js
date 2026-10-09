'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {network}=require('../core/dispatch'),{validatePair}=require('../core/contract');
for(const [check,detector] of Object.entries(network)){
 test(check+' public integration pair retains its actual hash and honest workload provenance',()=>{
  const directory=path.join(__dirname,'../../../docs/verification/owner-b/network',check,'2026-10-10');
  const receipt=JSON.parse(fs.readFileSync(path.join(directory,'receipts.json'),'utf8'));
  assert.equal(receipt.check_id,check);assert.equal(receipt.production_workload_confirmed,false);
  assert.equal(receipt.integration_build.source_state,'clean-committed');
  const synthetic=receipt.cases.filter(c=>c.capture_origin==='synthetic');assert.equal(synthetic.length,6);
  assert(synthetic.every(c=>c.persisted&&c.evidence==='verified'&&!c.production_workload_confirmed));
  const raw=fs.readFileSync(path.join(directory,'positive-pair.json')),pair=JSON.parse(raw.toString('utf8'));
  validatePair(pair.input,pair.result);assert.deepEqual(detector.evaluate(pair.input),pair.result);
  const record=receipt.cases.find(c=>c.scan_id===pair.input.scan_id);assert(record);
  assert.equal(crypto.createHash('sha256').update(raw).digest('hex'),record.pair_sha256);
  assert.equal(pair.input.sources.find(s=>s.data?.capture)?.data.capture.origin,'synthetic');
  const measured=path.join(directory,'measured-auditor-pair.json');
  if(fs.existsSync(measured)){
   const bytes=fs.readFileSync(measured),actual=JSON.parse(bytes.toString('utf8'));
   validatePair(actual.input,actual.result);assert.deepEqual(detector.evaluate(actual.input),actual.result);
   const proof=receipt.cases.find(c=>c.scan_id===actual.input.scan_id);assert.equal(proof.capture_origin,'auditor');
   assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),proof.pair_sha256);
  }
 });
}
