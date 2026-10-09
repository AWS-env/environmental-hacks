'use strict';
const j=require('../../core/jobs'),request=require('../../normalization/request-work');
function inspect(sources,input){
  const a=request.analyze(sources,input),m=a.m;
  j.text(m.sender,'sender');j.text(m.receiver,'receiver');
  j.requireValue(m.deployment_inventory_complete===true,'Missing complete deployment interaction inventory; absent queue config is not proof');
  const o=request.observations(sources,input,a);
  const ratio=j.number(input.context.minimum_peak_mean_ratio,'minimum_peak_mean_ratio',1),pressure=j.number(input.context.minimum_backpressure_ms,'minimum_backpressure_ms',1);
  j.requireValue(o.d.peak_arrivals_per_minute>=o.d.mean_arrivals_per_minute,'Inconsistent demand observations');
  if(!a.onPath||m.existing_decoupling||!m.async_completion_acceptable||m.requires_transaction_completion||o.d.mean_arrivals_per_minute===0||o.d.peak_arrivals_per_minute/o.d.mean_arrivals_per_minute<ratio||!o.d.events.some(e=>e.backpressure_ms>=pressure||e.downstream_failures>0))return [];
  return [j.finding(j.canonical([m.sender,m.receiver,m.operation]),'Architecture candidate requiring human review: synchronous optional work has observed burst/backpressure evidence',
    'Review SQS for buffered work or EventBridge for event fan-out. Specify ordering, idempotent consumption, retry/DLQ policy and status visibility; use a transactional outbox when state and enqueue must commit together. Confirm business semantics separately in the hub; this finding is not approval to introduce messaging.',
    [j.line(a.source,a.work.node.loc.start.line,a.work.node.loc.end.line),j.cite(a.metadata,'deployment_inventory_complete'),j.cite(a.metadata,'async_completion_acceptable'),j.cite(o.source,'events'),j.cite(o.source,'peak_arrivals_per_minute'),j.cite(o.source,'mean_arrivals_per_minute')],
    ['https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html','https://github.com/AWS-env/environmental-hacks/issues/184'],'low')];
}
module.exports={evaluate:input=>j.evaluateCheck(input,'JOB-06',inspect)};
