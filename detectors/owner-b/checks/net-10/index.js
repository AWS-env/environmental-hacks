'use strict';
const n=require('../../core/network'),{comparison}=require('../../normalization/benchmark');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const {benchmark:b,improves,costsAcceptable}=comparison(d,ctx),array=d.numeric_array;
 n.number(array.elements,'numeric element count',1);n.requireValue(Number.isInteger(array.elements),'Non-integral array length');
 n.digest(array.distribution_digest);n.text(array.precision,'precision requirements');n.requireValue(array.input_digest===b.input_digest,'Numeric benchmark input mismatch');
 n.boolean(array.lossless,'lossless requirement');n.number(ctx.min_array_elements,'numeric materiality',1);
 if(!array.lossless||array.elements<ctx.min_array_elements||!b.interoperability_allows_change||!improves||!costsAcceptable)return [];
 return [n.finding(d,ctx,source,['benchmark','numeric_array'],'numeric-format:'+b.baseline.format+':'+b.candidate.format,'A representative lossless numeric-array comparison improves the declared objective within the tested processing budget.','Use only the tested compression/format/precision configuration. Review packed versus fixed/varint fields and distribution; do not assume built-in compression determines efficiency.',[n.references.binary])];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-10',inspect),inspect};
