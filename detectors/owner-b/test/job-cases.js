'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {evaluate}=require('../core/dispatch'),{validatePair,compare}=require('../core/contract');

const clone=x=>JSON.parse(JSON.stringify(x));
const negative={
  'JOB-04':i=>{i.sources[0].data.jobs[1].expression='30 * * * *';},
  'JOB-03':i=>{i.sources[0].data.samples.forEach(s=>{s.busy_worker_seconds=1800;});},
  'JOB-05':i=>{i.sources[0].data.runs.forEach((r,n)=>{r.input_version='v'+n;});},
  'JOB-01':i=>{i.sources[1].data.heavy_operation=false;},
  'JOB-06':i=>{i.sources[2].data.peak_arrivals_per_minute=10;},
};
const exception={
  'JOB-04':i=>{i.sources[0].data.jobs[1].intentional_alignment=true;},
  'JOB-03':i=>{i.sources[0].data.policy.burst_reserve_workers=4;},
  'JOB-05':i=>{i.sources[0].data.policy.time_dependent=true;},
  'JOB-01':i=>{i.sources[1].data.requires_transaction_completion=true;},
  'JOB-06':i=>{i.sources[1].data.existing_decoupling=true;},
};
const malformed={
  'JOB-04':i=>{i.sources[0].data.jobs[0].expression='cron(0 1 * * * *)';},
  'JOB-03':i=>{i.sources[0].data.samples[0].busy_worker_seconds=-1;},
  'JOB-05':i=>{i.sources[0].data.runs[0].end='2025-01-01T00:00:00Z';},
  'JOB-01':i=>{i.sources[1].data.source_sha256='0'.repeat(64);},
  'JOB-06':i=>{i.sources[2].data.events[0].response_time='2025-01-01T00:00:00Z';},
};
function register(key,make){
  test(key+'-01 positive carries exact validated evidence',()=>{const input=make(),r=evaluate(input);assert.equal(r.status,'completed');assert.equal(r.findings.length,1);assert.deepEqual(r.coverage.evaluated_scope,['unit']);assert.deepEqual(r.measurements,[]);validatePair(input,r);});
  test(key+'-02 similar negative is fully evaluated',()=>{const i=make();negative[key](i);const r=evaluate(i);assert.equal(r.status,'completed');assert.equal(r.findings.length,0);});
  test(key+'-03 legitimate exception suppresses',()=>{const i=make();exception[key](i);const r=evaluate(i);assert.equal(r.status,'completed');assert.equal(r.findings.length,0);});
  test(key+'-04 missing evidence is not a clean scan',()=>{const i=make();i.sources=[];const r=evaluate(i);assert.equal(r.status,'unavailable');assert.deepEqual(r.coverage.evaluated_scope,[]);assert.equal(r.findings.length,0);});
  test(key+'-05 malformed data cannot certify scope',()=>{const i=make();malformed[key](i);const r=evaluate(i);assert.equal(r.status,'unavailable');assert.equal(r.findings.length,0);});
  test(key+'-06 stable identity, deterministic and tamper-resistant',()=>{const i=make(),r=evaluate(i);assert.deepEqual(evaluate(clone(i)),r);const altered=clone(i);altered.scan_id='next';altered.commit_sha='b'.repeat(40);for(const v of altered.sources.filter(x=>x.kind!=='static'))v.data.commit_sha=altered.commit_sha;assert.equal(evaluate(altered).findings[0].fingerprint,r.findings[0].fingerprint);const tamper=clone(r);tamper.findings[0].evidence[0].value='invented';assert.throws(()=>validatePair(i,tamper));const missing=clone(i);missing.sources=[];assert.deepEqual(compare(r,evaluate(missing)).no_longer_detected,[]);});
  test(key+' mixed scopes report partial coverage',()=>{const i=make();i.scope.push('missing');const r=evaluate(i);assert.equal(r.status,'partial');assert.equal(r.findings.length,1);assert.deepEqual(r.coverage.evaluated_scope,['unit']);});
}
module.exports={register};
