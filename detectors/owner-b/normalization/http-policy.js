'use strict';
const n=require('../core/network');
/** @param {any} value */
function headers(value){
 n.requireValue(value&&typeof value==='object'&&!Array.isArray(value),'Missing headers');
 const out={};for(const [name,v] of Object.entries(value)){n.requireValue(typeof v==='string'&&v.length<=4096,'Invalid header');const key=name.toLowerCase();n.requireValue(!Object.hasOwn(out,key),'Duplicate header casing');out[key]=v;}
 return out;
}
/** @param {string} value */
function cacheDirectives(value){return value.split(',').map(s=>s.trim().toLowerCase()).filter(Boolean);}
/** Explicit q=0 overrides wildcard and missing accepted encoding is not fabricated.
 * @param {string} value */
function acceptsCompression(value){
 const weights=new Map();
 for(const part of value.split(',')){const [raw,...params]=part.trim().toLowerCase().split(';'),q=params.find(s=>s.trim().startsWith('q='));if(!raw)continue;const weight=q?Number(q.trim().slice(2)):1;n.requireValue(Number.isFinite(weight)&&weight>=0&&weight<=1,'Malformed Accept-Encoding quality');n.requireValue(!weights.has(raw),'Duplicate encoding');weights.set(raw,weight);}
 return ['gzip','br','deflate','zstd'].some(k=>(weights.has(k)?weights.get(k):weights.get('*')||0)>0);
}
/** @param {any} http */
function response(http){
 n.requireValue(http&&http.headers_complete===true,'Response headers unavailable');
 n.text(http.method,'HTTP method');n.number(http.status,'HTTP status',100,599);
 n.requireValue(Number.isInteger(http.status),'Non-integral HTTP status');
 n.number(http.encoded_bytes,'encoded body bytes');n.number(http.decoded_bytes,'decoded body bytes');
 n.boolean(http.body_present,'body presence');n.boolean(http.range,'range response');
 return {...http,headers:headers(http.headers),request_headers:headers(http.request_headers)};
}
module.exports={headers,cacheDirectives,acceptsCompression,response};
