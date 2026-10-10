'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const f=require('./network-fixtures'),check=require('../checks/net-01'),{validatePair}=require('../core/contract');
test('NET-01: positive, negative, streaming, missing, cyclic and threshold cases',()=>{
 /** @type {[Function,number,string][]} */
 const cases=[
 [d=>d,1,'completed'],
 [d=>{d.spans=d.spans.slice(0,1);return d;},0,'completed'],
 [d=>{d.batch_policy.required_streaming=true;return d;},0,'completed'],
 [d=>{delete d.spans;return d;},0,'unavailable'],
 [d=>{d.spans[0].parent_id='s0';return d;},0,'unavailable'],
 [d=>{d.spans=d.spans.slice(0,4);return d;},0,'completed']
 ];
 for(const [change,count,status] of cases){const i=f.input('NET-01',change(f.evidence())),r=check.evaluate(i);assert.equal(r.findings.length,count);assert.equal(r.status,status);validatePair(i,r);assert.deepEqual(check.evaluate(i),r);if(count)assert.deepEqual(r.findings[0].evidence[0].value,i.sources[0].data.spans);}
});
test('NET-01: dependencies, mixed tenants and incomplete traces do not prove chatty waste',()=>{
 for(const change of [d=>d.spans.forEach(s=>s.dependencies=['prior']),d=>d.spans.forEach((s,i)=>s.authorization_key='tenant-'+i)]){const d=f.evidence();change(d);assert.equal(check.evaluate(f.input('NET-01',d)).findings.length,0);}
 const d=f.evidence();d.trace.complete=false;assert.equal(check.evaluate(f.input('NET-01',d)).status,'unavailable');
});
