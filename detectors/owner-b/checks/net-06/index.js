'use strict';
const n=require('../../core/network');
/** @param {any} d @param {any} ctx @param {any} source */
function inspect(d,ctx,source){
 const p=d.polling,e=d.event_alternative;
 n.number(p.polls,'poll count');n.number(p.changes,'changes');n.requireValue(p.changes<=p.polls,'Changes exceed polls');
 n.number(p.interval_seconds,'poll interval',0.001);n.number(p.freshness_seconds,'freshness SLA',0.001);
 n.boolean(p.reconciliation_required,'reconciliation');n.boolean(e.supported,'provider event support');n.boolean(e.reachable,'delivery reachability');
 n.text(e.documentation,'event documentation');n.requireValue(/^https:\/\//.test(e.documentation),'Invalid provider documentation');
 n.number(e.max_delivery_seconds,'delivery bound');n.boolean(e.recovery_supported,'missed event recovery');n.text(e.delivery_semantics,'delivery semantics');
 n.number(ctx.min_polls,'minimum polls',2);n.number(ctx.max_change_fraction,'change fraction',0,1);n.number(ctx.max_poll_interval_seconds,'poll materiality',0.001);
 if(!e.supported||!e.reachable||!e.recovery_supported||p.reconciliation_required||e.max_delivery_seconds>p.freshness_seconds||p.polls<ctx.min_polls||p.interval_seconds>ctx.max_poll_interval_seconds||!p.polls||p.changes/p.polls>ctx.max_change_fraction)return [];
 return [n.finding(d,ctx,source,['polling','event_alternative'],'polling-event-review','Frequent mostly unchanged polls have an evidenced event alternative compatible with the supplied freshness bound.','Human architecture review required: evaluate delivery loss, ordering, idempotency, reconciliation and operational cost before replacing polling. No webhook/subscription is enabled by the auditor.',[e.documentation,n.references.trace],'low')];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-06',inspect),inspect};
