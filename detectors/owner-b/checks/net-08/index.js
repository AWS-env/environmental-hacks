'use strict';
const n=require('../../core/network'),{comparison}=require('../../normalization/benchmark');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const {benchmark:b,improves,costsAcceptable}=comparison(d,ctx);
 n.requireValue(b.baseline.format==='json','NET-08 requires JSON baseline');n.boolean(b.candidate.binary,'binary format identity');
 if(!b.hot_path||!b.interoperability_allows_change||!b.candidate.binary||!improves||!costsAcceptable)return [];
 return [n.finding(d,ctx,source,['benchmark'],'json-binary:'+b.candidate.format,'Comparable semantically equivalent binary captures improve the declared hot-path objective within the supplied processing budget.','Consider only the tested schema/format and measured workload. Review interoperability, unknown fields, numeric precision and schema evolution; a smaller payload alone is not an energy result.',[n.references.binary])];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-08',inspect),inspect};
