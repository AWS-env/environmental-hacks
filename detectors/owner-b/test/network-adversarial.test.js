'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {network}=require('../core/dispatch'),{attachHar}=require('../collectors/network-artifact');
function fixture(key){return JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/network',key.toLowerCase()+'-01-input.json'),'utf8'));}
test('Network scopes report partial and missing source cannot certify no-longer-detected',()=>{
 for(const key of Object.keys(network)){const p=fixture(key);p.scope.push('missing:scope');const r=network[key].evaluate(p);assert.equal(r.status,'partial');assert.equal(r.coverage.evaluated_scope.length,1);assert.equal(r.findings.length,1);}
});
test('Node Agent import shadowing and globalAgent defaults do not prove churn',()=>{
 const p=fixture('NET-04');p.sources[1].content="const https=require('node:https');\nfunction load(https){const agent=new https.Agent();return https.request({agent});}";assert.equal(network['NET-04'].evaluate(p).status,'unavailable');
 const q=fixture('NET-04');q.sources[1].content="const https=require('node:https');\nfunction load(){const https={Agent:class{}};const agent=new https.Agent();return https.request({agent});}";assert.equal(network['NET-04'].evaluate(q).status,'unavailable');
});
test('Retry-After, tenant boundaries and non-idempotent advice are retained',()=>{
 const p=fixture('NET-05');p.sources[0].data.retry_policy.idempotent=false;assert.match(network['NET-05'].evaluate(p).findings[0].recommendation,/idempotency/);
 const q=fixture('NET-05');q.sources[0].data.attempts.forEach((a,i)=>a.authorization_key='tenant-'+i);assert.equal(network['NET-05'].evaluate(q).status,'unavailable');
 const r=fixture('NET-05');r.sources[0].data.retry_policy.backoff=true;r.sources[0].data.retry_policy.jitter=true;r.sources[0].data.attempts.forEach(a=>a.retry_after_ms=1000);assert.equal(network['NET-05'].evaluate(r).findings.length,1);
});
test('Personalized cache, Vary wildcard, tiny/bodyless HTTP and q=0 are exceptions',()=>{
 for(const change of [d=>d.cache_policy.personalized=true,d=>d.http.headers.vary='*']){const p=fixture('NET-03');change(p.sources[0].data);assert.equal(network['NET-03'].evaluate(p).findings.length,0);}
 for(const change of [d=>d.http.method='HEAD',d=>d.http.encoded_bytes=d.http.decoded_bytes=10,d=>d.compression_policy.secret_reflection=true]){const p=fixture('NET-07');change(p.sources[0].data);assert.equal(network['NET-07'].evaluate(p).findings.length,0);}
});
test('A/B environment, numeric precision and output mismatch are unavailable',()=>{
 for(const key of ['NET-08','NET-10'])for(const change of [b=>b.candidate.machine='other',b=>b.equivalence.precision_preserved=false,b=>b.candidate.output_digest='c'.repeat(64),b=>b.candidate.samples=[]]){const p=fixture(key);change(p.sources[0].data.benchmark);assert.equal(network[key].evaluate(p).status,'unavailable');}
});
test('Memory peak attribution and required conversion boundaries prevent false findings',()=>{
 const p=fixture('NET-09');p.sources[0].data.memory_profile.peak_bytes=1;assert.equal(network['NET-09'].evaluate(p).status,'unavailable');
 const q=fixture('NET-12');q.sources[0].data.conversion_profile.steps[2].semantic_digest='c'.repeat(64);assert.equal(network['NET-12'].evaluate(q).findings.length,0);
});
test('Browser cycles fail and reviewed independence supports only real waterfall evidence',()=>{
 const p=fixture('NET-11'),b=p.sources[0].data.browser;b.requests[0].dependencies=['q1'];b.requests[1].dependencies=['q0'];assert.equal(network['NET-11'].evaluate(p).status,'unavailable');
 const q=fixture('NET-11'),v=q.sources[0].data.browser;v.requests.forEach((r,i)=>r.request_digest=String(i+1).repeat(64));assert.equal(network['NET-11'].evaluate(q).findings.length,0);v.independence_reviewed=true;assert.equal(network['NET-11'].evaluate(q).findings.length,1);
});
test('HAR normalization strips secrets and requires a reviewed snapshot sidecar',()=>{
 const p=fixture('NET-11'),requests=p.sources[0].data.browser.requests,har={log:{version:'1.2',entries:requests.map(()=>({startedDateTime:'2026-10-10T00:00:00Z',time:50,request:{method:'GET',url:'https://example.invalid/catalog?token=secret',headers:[{name:'Authorization',value:'secret'}]},response:{content:{text:'secret'}}}))}};
 const sidecar={complete:true,repository_id:p.repository_id,commit_sha:p.commit_sha,window:{start:'2026-10-10T00:00:00Z',end:'2026-10-10T00:01:00Z'},origin:'synthetic',route:'catalog',interaction:'load',independence_reviewed:false,requests};
 const output=attachHar(p,har,sidecar);assert(!JSON.stringify(output).includes('secret'));assert.equal(network['NET-11'].evaluate(output).findings.length,1);
 assert.throws(()=>attachHar(p,har,{...sidecar,commit_sha:'c'.repeat(40)}),/snapshot/);
});
