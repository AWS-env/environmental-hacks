'use strict';
const n=require('../../core/network');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const p=d.payload,c=d.consumption;
 n.number(p.decoded_bytes,'decoded bytes');n.number(p.transferred_bytes,'wire bytes');n.text(p.encoding,'wire encoding');
 n.records(p.fields,'field sizes');n.unique(p.fields,'name');
 const total=p.fields.reduce((sum,f)=>sum+n.number(f.decoded_bytes,'field bytes'),0);
 n.requireValue(total<=p.decoded_bytes,'Field bytes exceed decoded payload');
 n.requireValue(c.complete===true&&Array.isArray(c.used_fields),'Incomplete consumer field-use contract');
 n.boolean(c.full_export_required,'export semantics');n.boolean(c.projection_supported,'projection support');
 const names=new Set(p.fields.map(f=>f.name));n.requireValue(c.used_fields.every(k=>names.has(k)),'Unknown consumed field');
 n.number(ctx.min_payload_bytes,'min payload bytes',1);n.number(ctx.min_unused_fraction,'unused fraction',0,1);
 if(c.full_export_required||!c.projection_supported||p.decoded_bytes<ctx.min_payload_bytes||p.decoded_bytes===0)return [];
 const unused=p.fields.filter(f=>!c.used_fields.includes(f.name)).reduce((sum,f)=>sum+f.decoded_bytes,0);
 if(unused/p.decoded_bytes<ctx.min_unused_fraction)return [];
 return [n.finding(d,ctx,source,['payload','consumption'],'response-projection','Transferred representation contains material fields unused by the complete supplied consumer contract.','Use an authorized projection/DTO for the observed consumer. Preserve optional branches and distinguish decoded from wire bytes; do not infer compressed savings.',[n.references.trace])];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-02',inspect),inspect};
