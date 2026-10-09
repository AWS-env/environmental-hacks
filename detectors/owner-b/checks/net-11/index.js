'use strict';
const n=require('../../core/network');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const browser=d.browser;n.requireValue(browser.complete===true&&browser.format_version===1,'Incomplete/unknown browser capture');n.text(browser.interaction,'interaction identity');
 n.number(ctx.min_duplicate_requests,'duplicate threshold',2);n.number(ctx.min_serial_gap_ms,'waterfall threshold',0);
 const entries=n.records(browser.requests,'browser requests');n.unique(entries,'id');
 for(const r of entries){n.text(r.method,'method');n.text(r.destination,'destination');n.digest(r.request_digest);n.digest(r.authorization_digest);n.digest(r.cache_key_digest);n.text(r.initiator,'initiator');n.number(r.start_ms,'request start');n.number(r.end_ms,'request end');n.requireValue(r.end_ms>=r.start_ms,'Negative browser timing');n.boolean(r.retry,'retry');n.boolean(r.poll,'poll');n.boolean(r.preflight,'preflight');n.boolean(r.redirect,'redirect');n.requireValue(Array.isArray(r.dependencies)&&r.dependencies.every(id=>entries.some(e=>e.id===id)),'Missing browser dependency metadata');}
 const eligible=entries.filter(r=>!r.retry&&!r.poll&&!r.preflight&&!r.redirect),groups=new Map(),found=[];
 n.requireValue(entries.length<=500,'Browser capture count bound');
 const visited=new Set();
 const visit=(id,seen)=>{n.requireValue(!seen.has(id),'Cyclic browser dependency');if(visited.has(id))return;const next=new Set(seen);next.add(id);for(const dep of entries.find(e=>e.id===id).dependencies)visit(dep,next);visited.add(id);};
 for(const r of entries)visit(r.id,new Set());
 for(const r of eligible){const key=n.canonical([r.method,r.destination,r.request_digest,r.authorization_digest,r.cache_key_digest]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
 for(const group of groups.values())if(group.length>=ctx.min_duplicate_requests&&group.every(r=>r.dependencies.length===0)){const r=group[0];found.push(n.finding(d,ctx,source,['browser'],browser.interaction+':'+r.destination+':duplicate','One interaction repeats identical independent requests under the same authorization and cache identity.','Deduplicate the evidenced request family within the interaction; preserve bodies, auth and cache boundaries.',[n.references.har]));}
 const independent=eligible.filter(r=>r.dependencies.length===0).sort((a,b)=>a.start_ms-b.start_ms);
 if(!found.length&&independent.length>=2){const serial=independent.every((r,i)=>i===0||r.start_ms>=independent[i-1].end_ms+ctx.min_serial_gap_ms);if(serial&&browser.independence_reviewed===true)found.push(n.finding(d,ctx,source,['browser'],browser.interaction+':independent-waterfall','Reviewed independent requests execute serially in the supplied interaction capture.','Use bounded parallel fetching after preserving the supplied dependency, authentication and rate-limit semantics. HAR timing alone is not independence proof.',[n.references.har]));}
 return [...new Map(found.map(f=>[f.identity,f])).values()];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-11',inspect),inspect};
