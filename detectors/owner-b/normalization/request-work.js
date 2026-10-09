'use strict';
const acorn=require('acorn'),crypto=require('node:crypto'),j=require('../core/jobs');
function name(node){if(node?.type==='Identifier')return node.name;if(node?.type==='MemberExpression'&&!node.computed){const left=name(node.object);return left&&left+'.'+node.property.name;}return null;}
function call(expr){return expr?.type==='AwaitExpression'?expr.argument:expr;}
/** Bounded direct-call model: named function, straight-line awaited/direct work, followed by response. */
function analyze(sources,input){
  const code=sources.filter(s=>s.kind==='static');j.requireValue(code.length===1,'Require one JavaScript source per route');
  const source=code[0];j.requireValue(Buffer.byteLength(source.content)<=262144,'JavaScript source exceeds limit');
  const metadata=j.structured(sources,'request-work-v1','artifact'),m=metadata.data;
  j.text(m.route_id,'route_id');j.text(m.handler,'handler');j.text(m.operation,'operation');j.text(m.response_call,'response_call');
  const digest=crypto.createHash('sha256').update(source.content).digest('hex');
  j.requireValue(m.source_sha256===digest&&m.locator===source.locator&&m.repository_id===input.repository_id&&m.commit_sha===input.commit_sha,'Call graph/source snapshot mismatch');
  j.requireValue(m.complete===true&&m.language==='javascript-es2022','Require complete supported handler metadata');
  for(const k of ['async_completion_acceptable','requires_transaction_completion','heavy_operation','existing_decoupling','metadata_reviewed'])j.boolean(m[k],k);
  j.requireValue(m.metadata_reviewed,'Require reviewed response/operation semantics');
  const ast=acorn.parse(source.content,{ecmaVersion:2022,sourceType:'module',locations:true});
  j.requireValue(ast.body.length<=1000,'AST top-level limit exceeded');
  const functions=ast.body.map(n=>n.type==='ExportNamedDeclaration'?n.declaration:n).filter(n=>n?.type==='FunctionDeclaration'&&n.id.name===m.handler);
  j.requireValue(functions.length===1,'Require unique named function handler');const fn=/** @type {import('acorn').FunctionDeclaration} */(functions[0]);
  j.requireValue(fn.body.body.length<=200,'Handler statement limit exceeded');
  const statements=fn.body.body;let work=null,response=null,workCount=0;
  for(let i=0;i<statements.length;i++){
    const s=statements[i];j.requireValue(['ExpressionStatement','ReturnStatement','VariableDeclaration','EmptyStatement'].includes(s.type),'Unsupported handler control flow');
    j.requireValue(s.type!=='ReturnStatement'||i===statements.length-1,'Early return/control flow unsupported');
    let expressions=[];
    if(s.type==='VariableDeclaration'){
      j.requireValue(s.kind==='const'&&s.declarations.every(d=>d.id.type==='Identifier'),'Mutable/alias handler bindings unsupported');expressions=s.declarations.map(d=>d.init);
    }else if(s.type==='ExpressionStatement')expressions=[s.expression];
    else if(s.type==='ReturnStatement')expressions=[s.argument];
    for(const expr of expressions){
      if(!expr)continue;const c=call(expr);
      j.requireValue(c.type==='CallExpression'||c.type==='Literal','Unsupported/nested work expression');
      if(c.type!=='CallExpression')continue;
      j.requireValue(name(c.callee),'Dynamic call target unsupported');
      j.requireValue(c.arguments.every(a=>['Identifier','Literal'].includes(a.type)),'Nested call/closure arguments unsupported');
      if(name(c.callee)===m.operation){workCount++;work={node:c,index:i,awaited:expr.type==='AwaitExpression'};}
      if(name(c.callee)===m.response_call){response={node:c,index:i};j.requireValue(i===statements.length-1,'Response must be final statement');}
    }
  }
  j.requireValue(response,'No direct terminal response call');
  j.requireValue(workCount<=1,'Multiple operation callsites unsupported');
  const onPath=work&&work.index<response.index;
  if(onPath)j.requireValue(work.awaited||m.operation_synchronous===true,'Unawaited asynchronous operation is not proven request-path work');
  return {source,metadata,m,work,onPath,digest};
}
function observations(sources,input,analysis){
  const source=j.structured(sources,'request-observations-v1','telemetry'),d=source.data,{start,end}=j.acquired(source,input);
  j.requireValue(d.route_id===analysis.m.route_id&&d.operation===analysis.m.operation&&d.source_sha256===analysis.digest,'Request telemetry/callsite mismatch');
  j.unique(d.events,'request_id');j.requireValue(d.events.length>=j.number(input.context.minimum_requests,'minimum_requests',2,1000),'Insufficient request samples');
  for(const e of d.events){
    const a=j.instant(e.operation_start),b=j.instant(e.operation_end),r=j.instant(e.response_time);j.requireValue(a>=start&&b<=r&&r<=end&&a<b,'Invalid/non-attributable request span');
    j.number(e.operation_ms,'operation_ms');j.requireValue(Math.abs(e.operation_ms-(b-a))<1,'Span duration mismatch');
    j.number(e.downstream_failures,'downstream_failures');j.number(e.backpressure_ms,'backpressure_ms');
  }
  j.number(d.peak_arrivals_per_minute,'peak arrivals');j.number(d.mean_arrivals_per_minute,'mean arrivals');
  return {source,d};
}
module.exports={analyze,observations};
