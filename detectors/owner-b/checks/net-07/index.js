'use strict';
const n=require('../../core/network'),h=require('../../normalization/http-policy');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const response=h.response(d.http),p=d.compression_policy;
 n.boolean(p.secret_reflection,'secret reflection');n.boolean(p.cpu_budget_available,'CPU budget');n.boolean(p.streaming,'streaming');
 n.number(ctx.min_response_bytes,'response threshold',1);
 if(!response.body_present||response.method==='HEAD'||[204,304,206].includes(response.status)||response.range||p.streaming||p.secret_reflection||!p.cpu_budget_available)return [];
 if(response.encoded_bytes<ctx.min_response_bytes||!h.acceptsCompression(response.request_headers['accept-encoding']||''))return [];
 const encoding=(response.headers['content-encoding']||'identity').toLowerCase();if(encoding!=='identity')return [];
 n.requireValue(response.encoded_bytes===response.decoded_bytes,'Identity response size mismatch');
 const type=(response.headers['content-type']||'').split(';')[0].trim().toLowerCase();
 if(!/^(text\/|application\/(json|javascript|xml|.*\+json|.*\+xml)$)/.test(type))return [];
 return [n.finding(d,ctx,source,['http','compression_policy'],'response-compression','A material compressible body is sent with identity encoding to a client accepting compression.','Benchmark a mutually accepted encoding and level within the declared CPU/latency budget. Exclude secret-reflection responses; compare real wire bytes rather than inventing compressed size.',[n.references.http])];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-07',inspect),inspect};
