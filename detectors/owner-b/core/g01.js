'use strict';
const crypto=require('node:crypto');
const flow=require('./local-flow');
const api=require('./php-api-models');
const {analyzeSql,DIALECTS}=require('../normalization/sql');
const {validate,validatePair,fingerprint,envelope,canonical}=require('./contract');
const VERSION='1.0.0';
const CHECKS={'DB-34':136,'DB-06':108,'DB-05':107,'DB-16':118};
const NOTES='Bounded PHP local analysis; declared API and ordinary-table identities are connector assertions. No measured CPU, energy or carbon savings. No customer code is executed.';
/** @param {any} c */
function settings(c) {
  if(c.language!=='php' || c.php_version!=='8.2' || c.rule_version!=='g01-1')throw new Error('Require PHP 8.2 and rule_version g01-1');
  if(!['runtime','static_candidate'].includes(c.mode))throw new Error('Require explicit runtime or static_candidate mode');
  if(!c.db_apis||!Object.keys(c.db_apis).length||!c.cache_apis||!Array.isArray(c.ordinary_tables)||!DIALECTS[c.sql_dialect])throw new Error('Missing API/table/dialect metadata');
  if(canonical(c.limits)!==canonical(flow.LIMITS))throw new Error('Require supported explicit parsing limits');
  api.validateModels(c);
}
/** @param {any} node */
function sqlConstruction(node) {
  let found=false;
  flow.walk(node,n=>{if(n.kind==='string' && /^\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|WITH)\b/i.test(n.value))found=true;});
  return found && ['string','bin','encapsed'].includes(node?.kind);
}
/** @param {any} node @param {string} name @param {string} miss */
function hit(node,name,miss) {
  if(miss==='isHit') {const m=api.method(node);return m?.receiver===name && m.name==='ishit' && m.args.length===0;}
  if(node?.kind!=='bin'||node.type!=='!==')return false;
  const sentinel=n=>miss==='null'?n?.kind==='nullkeyword':n?.kind==='boolean' && n.value===false;
  return (flow.variable(node.left)===name && sentinel(node.right))||(flow.variable(node.right)===name && sentinel(node.left));
}
/** @param {any} statement @param {any} context @param {Map<string,any>} cacheBindings */
function cacheGuard(statement,context,cacheBindings) {
  if(statement.kind!=='if'||statement.alternate)return null;
  const test=statement.test;
  let lookup=null,name=null;
  if(test?.kind==='bin' && test.type==='!==') {
    for(const side of [test.left,test.right])if(side?.kind==='assign') {
      lookup=api.cacheCall(side.right,context);name=flow.variable(side.left);
      if(lookup) {
        const replaced={...test,left:test.left===side?side.left:test.left,right:test.right===side?side.left:test.right};
        if(!hit(replaced,name,lookup.miss))return null;
      }
    }
  }
  if(!lookup)for(const [binding,value]of cacheBindings)if(hit(test,binding,value.lookup.miss)){lookup=value.lookup;name=binding;}
  if(!lookup || !name)return null;
  const children=statement.body.kind==='block'?statement.body.children:[statement.body];
  // Pure cache-hit return only; helpers, throws, query execution or partial returns cannot prove the bypass.
  if(children.length!==1||children[0].kind!=='return')return null;
  let calls=false;flow.walk(children[0],n=>{if(['call','assign','yield','include','eval'].includes(n.kind))calls=true;});
  if(calls)return null;
  return {lookup,statement,exit:children[0]};
}
/** @param {any} scope @param {any} context @param {string} check */
function inspectScope(scope,context,check) {
  const definitions=new Map(),cacheBindings=new Map(),setups=new Map();
  const candidates=[],executions=[];
  const counts=new Map();
  let unsafe=null;
  flow.walk(scope.node,n=>{
    if(n.kind==='assign') {const name=flow.variable(n.left);if(name)counts.set(name,(counts.get(name)||0)+1);}
    if(['closure','arrowfunc','eval','include','global','static','yield','goto','try','switch','while','do','for','foreach'].includes(n.kind))unsafe='Unsupported dynamic/escaping/control-flow construct';
    if(n.kind==='variable' && typeof n.name!=='string')unsafe='Dynamic variable unsupported';
    if(n.kind==='assign' && (n.operator!=='=' || n.byref))unsafe='Reference/compound assignment unsupported';
    if(n!==scope.node && ['function','method'].includes(n.kind))unsafe='Nested named function unsupported';
    if(n.kind==='call')for(const receiver of [...Object.keys(context.db_apis),...Object.keys(context.cache_apis)]) {
      if(flow.uses(n,receiver) && !api.dbCall(n,context) && !api.cacheCall(n,context))unsafe='Declared API receiver escapes or has unsupported method';
    }
  });
  if(scope.node.arguments?.some(p=>p.byref)||scope.node.byref)unsafe='By-reference function unsupported';
  for(const name of [...Object.keys(context.db_apis),...Object.keys(context.cache_apis)])if(counts.has(name))unsafe='Declared API receiver reassigned';
  if(scope.name==='<top-level>')unsafe='Require named function/method consumption scope';
  if(unsafe)throw new Error(unsafe+' in '+scope.name);
  let ordinal=0;
  for(let index=0;index<scope.statements.length;index++) {
    const statement=scope.statements[index],expr=flow.expression(statement);
    if(statement.kind==='if') {
      const guard=check==='DB-34' && cacheGuard(statement,context,cacheBindings);
      if(!guard)throw new Error('Unsupported branch/cache-hit semantics in '+scope.name);
      for(const definition of definitions.values())if(definition.sql && !definition.used && !definition.executed && !flow.uses(statement,definition.name))definition.guard=guard;
      continue;
    }
    if(!['expressionstatement','return','throw','noop'].includes(statement.kind))throw new Error('Unsupported statement '+statement.kind+' in '+scope.name);
    const assignment=expr?.kind==='assign'?expr:null;
    const binding=assignment&&flow.variable(assignment.left);
    if(assignment && !binding)throw new Error('Nonlocal assignment unsupported');
    const rhs=assignment?assignment.right:statement.kind==='return'?statement.expr:expr;
    const m=api.method(rhs),call=api.dbCall(rhs,context);
    if(m && ['query','prepare','execute','setquery','loadobjectlist','loadassoclist','loadresult','loadobject','loadassoc'].includes(m.name) && !context.db_apis[m.receiver])throw new Error('Database API identity unavailable for '+m.receiver);
    let nestedDb=false;
    flow.walk(rhs,n=>{if(n!==rhs && api.dbCall(n,context))nestedDb=true;});
    if(nestedDb)throw new Error('Nested execution expression unsupported');
    if(m && context.db_apis[m.receiver] && !call)throw new Error('Unsupported database method '+m.name);
    let record=null;
    if(call) {
      let definition=flow.variable(call.sql)&&definitions.get(flow.variable(call.sql));
      let text=call.sql?flow.literal(call.sql,definitions):null;
      if(call.stage==='setup') {
        setups.set(call.receiver,{definition,text,statement});
      } else {
        if(!call.sql) {const setup=setups.get(call.receiver);definition=setup?.definition;text=setup?.text??null;}
        record={node:statement,call,binding,scope:scope.name,ordinal:++ordinal,index,definition,text,setup:!call.sql?setups.get(call.receiver):null,used:statement.kind==='return',overwritten:null};
        executions.push(record);
        if(check==='DB-34' && definition?.sql && definition.guard && !definition.used && !definition.executed) {
          candidates.push({identity:`${scope.name}:construction:${definition.ordinal}:${definition.name}:${definition.guard.lookup.receiver}:${call.api}`,nodes:[definition.node,definition.guard.lookup.node,definition.guard.statement,definition.guard.exit,...(record.setup?[record.setup.statement]:[]),statement],operation:record,summary:'SQL is constructed before a proven cache-hit return',recommendation:'Check cache first; construct this SQL definition only on the miss path.'});
        }
        if(definition)definition.executed=true;
      }
    }
    // Mark all uses of previous SQL/result/cache bindings before replacing a definition.
    for(const definition of definitions.values()) {
      const permitted=call && ((flow.variable(call.sql)===definition.name) || (!call.sql && record?.definition===definition));
      if(flow.uses(rhs,definition.name) && !permitted)definition.used=true;
      // PHP helpers may accept reference parameters even when no '&' is visible at the callsite.
      let escapes=false;
      flow.walk(rhs,n=>{if(n.kind==='call' && (n.arguments||[]).some(arg=>flow.uses(arg,definition.name)) && !api.dbCall(n,context))escapes=true;});
      if(escapes)definition.literal=null;
    }
    for(const name of cacheBindings.keys()) {
      let escapes=false;
      flow.walk(rhs,n=>{if(n.kind==='call' && (n.arguments||[]).some(arg=>flow.uses(arg,name)))escapes=true;});
      if(escapes)cacheBindings.delete(name);
    }
    for(const prior of executions)if(prior!==record && prior.binding) {
      if(!prior.overwritten && flow.uses(rhs,prior.binding))prior.used=true;
      if(binding===prior.binding && !prior.overwritten)prior.overwritten=record||{nonquery:true};
    }
    if(binding) {
      const constant=flow.literal(rhs,definitions);
      const proofNodes=[];
      flow.walk(rhs,n=>{if(flow.variable(n)&&definitions.has(flow.variable(n)))proofNodes.push(...definitions.get(flow.variable(n)).proofNodes);});
      proofNodes.push(statement);
      const definition={name:binding,node:statement,proofNodes:[...new Set(proofNodes)],literal:counts.get(binding)===1?constant:null,sql:sqlConstruction(rhs),ordinal:index+1,used:false,executed:false,guard:null};
      definitions.set(binding,definition);
      cacheBindings.delete(binding);
      const lookup=api.cacheCall(rhs,context);
      if(lookup)cacheBindings.set(binding,{lookup});
      // Updating a SQL definition invalidates a prior Joomla setup referencing that binding.
      for(const [receiver,setup]of setups)if(setup.definition?.name===binding)setups.delete(receiver);
    }
    if(['return','throw'].includes(statement.kind))break;
  }
  if(check!=='DB-34')for(const operation of executions) {
    if(operation.text===null)throw new Error('SQL definition/parameter semantics unavailable at '+scope.name+':'+operation.ordinal);
    const sql=analyzeSql(operation.text,context);
    if(!sql.safe)continue;
    let selected=false,summary='',recommendation='';
    if(check==='DB-06') {selected=sql.knownEmpty;summary='SELECT predicate cannot produce qualifying rows';recommendation='Replace this read with an equivalent empty result after preserving the caller result type and error semantics.';}
    if(check==='DB-05') {selected=!!operation.binding && !operation.used && !!operation.overwritten?.call;summary='Read result is overwritten by a second read before any local use';recommendation='Review removal of the first read; preserve transaction, error and side-effect semantics.';}
    if(check==='DB-16') {selected=!operation.used;summary='Executed read result has no supported observable local use';recommendation='Review removing this read after confirming ordinary-table, error and transaction assumptions.';}
    if(selected)candidates.push({identity:`${scope.name}:execution:${operation.ordinal}:${operation.call.api}${check==='DB-05'?':'+operation.binding+':reload:'+operation.overwritten.ordinal:''}`,nodes:[...(operation.definition?operation.definition.proofNodes:[]),...(operation.setup?[operation.setup.statement]:[]),operation.node,...(check==='DB-05'?[operation.overwritten.node]:[])],operation,summary,recommendation});
  }
  return {candidates,executions};
}
/** @param {any} source @param {any} node */
function cite(source,node) {
  const start=node.loc.start.line,end=node.loc.end.line;
  // PHP locations count LF, preserve whole physical lines and leading whitespace.
  const lines=source.content.split(/\r\n|\n|\r/);
  return {source_id:source.source_id,kind:'static',locator:source.locator,line_start:start,value:lines.slice(start-1,end).join('\n')};
}
/** @param {any} source @param {any} input @param {any} staticSource */
function events(source,input,staticSource) {
  const d=source?.data;
  if(!d || d.format!=='query-event-v1' || d.acquisition?.status!=='complete' || !Array.isArray(d.events))throw new Error('Missing complete query-event-v1 acquisition');
  const start=Date.parse(d.acquisition.start),end=Date.parse(d.acquisition.end);
  if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end||!/(Z|[+-]\d\d:\d\d)$/.test(d.acquisition.start)||!/(Z|[+-]\d\d:\d\d)$/.test(d.acquisition.end))throw new Error('Invalid acquisition window');
  const digest=crypto.createHash('sha256').update(staticSource.content,'utf8').digest('hex');
  const ids=new Set();
  for(const e of d.events) {
    if(!e || !['event_id','request_id','transaction_id','repository_id','commit_sha','source_sha256','timestamp','function','operation_id','locator','sql','status','dialect'].every(k=>typeof e[k]==='string'&&e[k]))throw new Error('Malformed query event');
    if(ids.has(e.event_id))throw new Error('Duplicate event_id');
    ids.add(e.event_id);
    const t=Date.parse(e.timestamp);
    if(!Number.isFinite(t)||t<start||t>=end||!/(Z|[+-]\d\d:\d\d)$/.test(e.timestamp))throw new Error('Event outside acquired window');
    if(e.repository_id!==input.repository_id||e.commit_sha!==input.commit_sha||e.source_sha256!==digest||e.locator!==staticSource.locator||e.dialect!==input.context.sql_dialect)throw new Error('Query event snapshot/dialect mismatch');
    if(e.status!=='success')throw new Error('Failed/unknown execution cannot confirm redundancy');
  }
  return d.events;
}
/** @param {any} candidate @param {any[]} records */
function correlate(candidate,records) {
  const op=candidate.operation;
  const matches=o=>records.filter(e=>e.function===o.scope && e.operation_id===String(o.ordinal) && e.sql===o.text);
  const first=matches(op);
  if(!first.length)return false;
  if(!op.overwritten?.call)return true;
  const second=matches(op.overwritten);
  // Same request/transaction plus temporal execution order; templates alone never establish correlation.
  return first.some(a=>second.some(b=>a.request_id===b.request_id&&a.transaction_id===b.transaction_id&&Date.parse(a.timestamp)<Date.parse(b.timestamp)));
}
/** Pure read-only one-check evaluator. Invalid envelopes are rejected; missing support is explicit coverage.
 * @param {any} input */
function evaluate(input) {
  validate(input);
  if(input.kind!=='input'||!CHECKS[input.check_id])throw new Error('Unsupported G01 check');
  const result=envelope(input);
  let settingsError=null;
  try {settings(input.context);if(input.detector_version!==VERSION)throw new Error('Unsupported detector version');}catch(e){settingsError=e.message;}
  let parseFailed=false;
  for(const scope of input.scope) {
    try {
      if(settingsError)throw new Error(settingsError);
      const sources=input.sources.filter(s=>s.scope_id===scope);
      const staticSources=sources.filter(s=>s.kind==='static');
      if(staticSources.length!==1)throw new Error('Require exactly one PHP source per file scope');
      const source=staticSources[0];
      let ast;
      try {ast=flow.parse(source.content,source.locator);}catch(e){parseFailed=true;throw e;}
      const findings=[];
      const scopes=flow.scopes(ast);
      const analyses=scopes.map(s=>inspectScope(s,input.context,input.check_id));
      const hybrid=['DB-05','DB-16'].includes(input.check_id);
      const telemetry=sources.filter(s=>s.kind==='telemetry');
      let records=[];
      if(hybrid && input.context.mode==='runtime') {
        if(telemetry.length!==1)throw new Error('Runtime mode requires one correlated telemetry source');
        records=events(telemetry[0],input,source);
      }
      for(const analysis of analyses)for(const candidate of analysis.candidates) {
        if(hybrid && input.context.mode==='runtime' && !correlate(candidate,records))throw new Error('Source-to-log execution correlation missing');
        /** @type {Array<{source_id:string,kind:string,locator:string,value:unknown,line_start?:number,field?:string}>} */
        const evidence=candidate.nodes.map(n=>cite(source,n));
        if(hybrid && input.context.mode==='runtime')evidence.push({source_id:telemetry[0].source_id,kind:'telemetry',locator:telemetry[0].locator,field:'events',value:telemetry[0].data.events});
        const identity=candidate.identity;
        findings.push({scope_id:scope,identity,fingerprint:fingerprint(input.repository_id,input.check_id,scope,identity),summary:(hybrid && input.context.mode==='static_candidate'?'Static candidate (execution unverified): ':'')+candidate.summary,confidence:hybrid?'medium':'high',recommendation:candidate.recommendation,references:[`https://github.com/AWS-env/environmental-hacks/issues/${CHECKS[input.check_id]}`,'https://taoxie.cs.illinois.edu/publications/icsme20-dbperf.pdf'],evidence});
      }
      result.coverage.evaluated_scope.push(scope);
      result.findings.push(...findings);
    }catch(e){result.coverage.limitations.push(scope+': '+e.message);}
  }
  result.status=result.coverage.evaluated_scope.length===input.scope.length?'completed':result.coverage.evaluated_scope.length?'partial':parseFailed?'error':'unavailable';
  result.coverage.limitations.push(NOTES);
  if(['DB-05','DB-16'].includes(input.check_id) && input.context.mode==='static_candidate')result.coverage.limitations.push('Static candidate mode: no runtime execution or real-data gate certified.');
  validatePair(input,result);
  return result;
}
module.exports={evaluate,VERSION,CHECKS,inspectScope,events};
