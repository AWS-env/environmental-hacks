'use strict';
const n=require('../core/network');
/** @param {number[]} values */
function median(values){const sorted=[...values].sort((a,b)=>a-b),mid=Math.floor(sorted.length/2);return sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2;}
/** Validated summaries keep wall/CPU timing explicit; no implicit energy model.
 * @param {any} d @param {any} ctx */
function comparison(d,ctx){
 const b=d.benchmark;n.requireValue(b.format_version===1,'Unsupported benchmark format');
 n.number(ctx.min_repetitions,'min repetitions',3);n.number(ctx.min_improvement_fraction,'improvement',0,1);n.number(ctx.max_noise_fraction,'noise tolerance',0,1);n.number(ctx.max_cpu_regression_fraction,'CPU tradeoff',0,10);
 n.requireValue(['bytes','encode_ms','decode_ms'].includes(ctx.objective),'Unknown comparison objective');
 n.digest(b.input_digest);n.digest(b.schema_digest);n.text(b.workload,'workload');n.number(b.operations,'operation count',1);
 n.boolean(b.hot_path,'hot-path evidence');n.boolean(b.interoperability_allows_change,'interoperability');
 n.requireValue(b.equivalence?.verified===true&&b.equivalence.unknown_fields_preserved===true&&b.equivalence.precision_preserved===true,'Semantic roundtrip equivalence incomplete');
 n.digest(b.equivalence.output_digest);
 const stats=[];
 for(const run of [b.baseline,b.candidate]){
  n.requireValue(run.input_digest===b.input_digest&&run.output_digest===b.equivalence.output_digest&&run.schema_digest===b.schema_digest,'A/B semantic/input mismatch');
  n.text(run.format,'representation');n.text(run.runtime,'runtime');n.text(run.machine,'machine');n.text(run.tool_version,'tool version');n.text(run.warmup_policy,'warmup policy');n.text(run.compression,'compression config');
  n.requireValue(['wall','cpu'].includes(run.timing_kind)&&run.time_unit==='milliseconds'&&run.size_unit==='bytes','Unknown benchmark units');
  const samples=n.records(run.samples,'benchmark samples');n.requireValue(samples.length>=ctx.min_repetitions,'Insufficient repetitions');
  const summary={};for(const key of ['bytes','encode_ms','decode_ms']){const values=samples.map(s=>n.number(s[key],key));const mid=median(values),mad=median(values.map(v=>Math.abs(v-mid)));n.requireValue(mid>0&&mad/mid<=ctx.max_noise_fraction,'Zero/noisy comparison');summary[key]=mid;}
  stats.push(summary);
 }
 const a=b.baseline,c=b.candidate;n.requireValue(['runtime','machine','tool_version','warmup_policy','timing_kind'].every(k=>a[k]===c[k]),'Incomparable benchmark environments');
 const [baseline,candidate]=stats;
 const improves=(baseline[ctx.objective]-candidate[ctx.objective])/baseline[ctx.objective]>=ctx.min_improvement_fraction;
 const costsAcceptable=['encode_ms','decode_ms'].every(k=>candidate[k]<=baseline[k]*(1+ctx.max_cpu_regression_fraction));
 return {benchmark:b,baseline,candidate,improves,costsAcceptable};
}
module.exports={comparison,median};
