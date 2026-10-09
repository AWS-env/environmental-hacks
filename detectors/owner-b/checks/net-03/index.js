'use strict';
const n=require('../../core/network'),h=require('../../normalization/http-policy');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const response=h.response(d.http),p=d.cache_policy,m=d.cache_observations;
 n.boolean(p.safe_to_cache,'cache safety');n.boolean(p.personalized,'personalization');n.boolean(p.authorization_sensitive,'authorization boundary');
 n.number(p.freshness_seconds,'freshness budget');n.requireValue(p.vary_complete===true,'Unknown Vary/cache key');n.digest(p.equivalent_key_digest);
 n.number(m.eligible_requests,'eligible requests');n.number(m.cache_hits,'cache hits');n.number(m.origin_requests,'origin requests');
 n.requireValue(m.cache_hits+m.origin_requests===m.eligible_requests,'Incompatible hit/origin denominator');
 n.requireValue(m.identical_representation===true,'Unknown resource equivalence');
 n.number(ctx.min_repeated_requests,'min repeated requests',2);
 const directives=h.cacheDirectives(response.headers['cache-control']||'');
 if(!p.safe_to_cache||p.personalized||p.authorization_sensitive||!p.freshness_seconds||directives.includes('no-store')||directives.some(v=>v.startsWith('private'))||response.headers.vary==='*')return [];
 if(response.method!=='GET'||response.status!==200||!response.body_present||response.range)return [];
 if(response.headers.etag||response.headers['last-modified']||directives.some(v=>/^(s-maxage|max-age)=[1-9]\d*$/.test(v)))return [];
 if(m.eligible_requests<ctx.min_repeated_requests||m.cache_hits>0)return [];
 return [n.finding(d,ctx,source,['http','cache_policy','cache_observations'],'response-cache','Equivalent safely cacheable responses repeatedly reach the origin without usable freshness or validators.','Apply the reviewed freshness and Vary/authorization policy; consider validators or private caching where appropriate. Never cache sensitive responses publicly.',[n.references.cache])];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-03',inspect),inspect};
