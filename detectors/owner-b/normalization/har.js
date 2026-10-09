'use strict';
const crypto=require('node:crypto'),n=require('../core/network');
const {URL}=require('node:url');
const digest=x=>crypto.createHash('sha256').update(n.canonical(x)).digest('hex');
/** Sanitized HAR alone lacks dependency/body/auth equivalence. A reviewed sidecar is mandatory.
 * @param {any} har @param {any} sidecar */
function normalizeHar(har,sidecar){
 n.requireValue(har.log?.version==='1.2','Unsupported HAR version');n.requireValue(sidecar.complete===true,'Incomplete HAR sidecar');
 const entries=n.records(har.log.entries,'HAR entries');n.requireValue(entries.length<=500,'HAR count bound');
 const requests=entries.map((e,index)=>{
  const meta=sidecar.requests[index];n.requireValue(meta,'Missing request identity/dependency sidecar');
  n.text(e.request?.method,'HAR method');n.number(e.time,'HAR elapsed');
  const start=n.instant(e.startedDateTime),url=new URL(e.request.url);n.requireValue(['http:','https:'].includes(url.protocol),'Unsupported HAR URL');
  n.requireValue(!url.username&&!url.password,'Credential-bearing HAR URL');
  n.digest(meta.request_digest);n.digest(meta.authorization_digest);n.digest(meta.cache_key_digest);
  // Do not retain headers, cookies, query parameters, body text, raw hostname or response content.
  return {id:meta.id,method:e.request.method,destination:meta.destination,request_digest:meta.request_digest,authorization_digest:meta.authorization_digest,cache_key_digest:meta.cache_key_digest,initiator:meta.initiator,start_ms:start,end_ms:start+e.time,retry:meta.retry,poll:meta.poll,preflight:meta.preflight,redirect:meta.redirect,dependencies:meta.dependencies};
 });
 n.requireValue(sidecar.requests.length===entries.length,'HAR/sidecar count mismatch');
 return {format_version:1,complete:true,interaction:sidecar.interaction,independence_reviewed:sidecar.independence_reviewed,requests,har_digest:digest(har),sidecar_digest:digest(sidecar)};
}
module.exports={normalizeHar};
