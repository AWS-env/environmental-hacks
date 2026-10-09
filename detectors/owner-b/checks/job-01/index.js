'use strict';
const j=require('../../core/jobs'),request=require('../../normalization/request-work');
function inspect(sources,input){
  const a=request.analyze(sources,input),m=a.m;
  if(!a.onPath||m.existing_decoupling||!m.heavy_operation||!m.async_completion_acceptable||m.requires_transaction_completion)return [];
  const evidence=[j.line(a.source,a.work.node.loc.start.line,a.work.node.loc.end.line),j.cite(a.metadata,'async_completion_acceptable'),j.cite(a.metadata,'requires_transaction_completion')];
  if(input.context.mode==='runtime'){
    const o=request.observations(sources,input,a),threshold=j.number(input.context.minimum_operation_ms,'minimum_operation_ms',1);
    if(!o.d.events.every(e=>e.operation_ms>=threshold))return [];evidence.push(j.cite(o.source,'events'));
  }
  return [j.finding(j.canonical([m.route_id,m.operation]),(input.context.mode==='candidate'?'Static candidate (runtime cost unverified): ':'Observed request-path work: ')+m.operation+' completes before the response although eventual completion is allowed',
    'Review moving this optional work to a queue/worker with an accepted response and status endpoint. Define idempotency keys, retry limits, dead letters and enqueue-failure behavior. Keep mandatory authorization/payment completion on the request path.',evidence,
    ['https://nodejs.org/en/learn/asynchronous-work/dont-block-the-event-loop','https://github.com/AWS-env/environmental-hacks/issues/180'],input.context.mode==='candidate'?'low':'medium')];
}
module.exports={evaluate:input=>j.evaluateCheck(input,'JOB-01',inspect)};
