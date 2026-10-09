'use strict';
const acorn=require('acorn'),n=require('../../core/network');
/** Bounded Node http(s).Agent constructors and their linked request agent option only.
 * @param {any} source @param {any} lifecycle */
function construction(source,lifecycle){
 n.requireValue(source&&source.kind==='static'&&Buffer.byteLength(source.content)<=262144,'Require bounded JavaScript source');
 /** @type {any} */
 const ast=acorn.parse(source.content,{ecmaVersion:2022,sourceType:'module',locations:true});
 const imports=new Map(),constructorNames=new Set();
 for(const statement of ast.body){
  if(statement.type==='ImportDeclaration'&&['node:http','node:https','http','https'].includes(statement.source.value)){
   for(const item of statement.specifiers){if(item.type==='ImportSpecifier'&&item.imported.name==='Agent')constructorNames.add(item.local.name);if(item.type==='ImportNamespaceSpecifier'||item.type==='ImportDefaultSpecifier')imports.set(item.local.name,statement.source.value);}
  }
  if(statement.type==='VariableDeclaration')for(const decl of statement.declarations){if(decl.init?.type==='CallExpression'&&decl.init.callee.name==='require'&&['node:http','node:https','http','https'].includes(decl.init.arguments[0]?.value)){if(decl.id.type==='Identifier')imports.set(decl.id.name,decl.init.arguments[0].value);if(decl.id.type==='ObjectPattern')for(const p of decl.id.properties)if(p.key.name==='Agent')constructorNames.add(p.value.name);}}
 }
 const fn=ast.body.find(s=>s.type==='FunctionDeclaration'&&s.id?.name===lifecycle.handler);
 n.requireValue(fn&&fn.body.type==='BlockStatement'&&fn.body.body.length<=100,'Unsupported handler shape');
 n.requireValue(!fn.params.some(p=>p.type!=='Identifier'||imports.has(p.name)||constructorNames.has(p.name)),'Shadowed or unsupported handler parameters');
 for(const st of fn.body.body)if(st.type==='VariableDeclaration')for(const decl of st.declarations)n.requireValue(decl.id.type==='Identifier'&&!imports.has(decl.id.name)&&!constructorNames.has(decl.id.name),'Shadowed or unsupported local binding');
 const isAgent=node=>node?.type==='NewExpression'&&(constructorNames.has(node.callee.name)||node.callee.type==='MemberExpression'&&!node.callee.computed&&node.callee.property.name==='Agent'&&imports.has(node.callee.object.name));
 const found=[];
 for(const statement of fn.body.body){
  if(statement.type!=='VariableDeclaration')continue;
  for(const decl of statement.declarations){
   if(decl.id.type!=='Identifier'||!isAgent(decl.init))continue;
   n.requireValue(statement.kind==='const','Mutable Agent binding unsupported');
   let used=false;
   for(const st of fn.body.body){
    const expr=st.type==='ExpressionStatement'?st.expression:st.type==='ReturnStatement'?st.argument:null;
    if(expr?.type==='CallExpression'&&expr.callee.type==='MemberExpression'&&!expr.callee.computed&&imports.has(expr.callee.object.name)&&['request','get'].includes(expr.callee.property.name)){
     used=expr.arguments.some(a=>a.type==='ObjectExpression'&&a.properties.some(p=>p.type==='Property'&&!p.computed&&p.key.name==='agent'&&p.value.type==='Identifier'&&p.value.name===decl.id.name))||used;
    }
   }
   if(used)found.push(decl.init.loc.start.line);
  }
 }
 return found;
}
/** @param {any} d @param {any} ctx @param {any} source @param {any[]} sources */
function inspect(d,ctx,source,sources){
 const p=d.client_lifecycle;n.text(p.handler,'handler');n.requireValue(/^\d+\.\d+\.\d+$/.test(p.node_version),'Exact Node version required');
 n.requireValue(p.library==='node:http-agent','Unsupported library');
 n.boolean(p.per_request_handler,'request lifetime');n.boolean(p.isolation_required,'tenant isolation');n.boolean(p.shared_transport,'shared transport');
 const src=sources.find(s=>s.kind==='static'&&s.locator===p.source_locator);n.requireValue(src,'Source mapping missing');
 const lines=construction(src,p);if(!lines.length||!p.per_request_handler||p.isolation_required||p.shared_transport)return [];
 if(ctx.mode==='runtime'){n.number(d.connection_observations.new_connections,'new connections');n.number(d.connection_observations.requests,'request count');n.requireValue(d.connection_observations.handler===p.handler,'Socket attribution mismatch');n.number(ctx.min_connection_ratio,'connection ratio',0,1);if(!d.connection_observations.requests||d.connection_observations.new_connections/d.connection_observations.requests<ctx.min_connection_ratio)return [];}
 const finding=n.finding(d,ctx,source,['client_lifecycle',...(ctx.mode==='runtime'?['connection_observations']:[])],p.handler+':http-agent','A request handler creates and uses a fresh explicit Node HTTP Agent instead of reusing its pool.','Reuse a compatible Agent outside the request handler when reviewed isolation permits. Modern Node globalAgent keep-alive defaults are not themselves a defect.',[n.references.node]);
 finding.evidence.push(...lines.map(line=>n.line(src,line)));return [finding];
}
module.exports={evaluate:input=>n.evaluateCheck(input,'NET-04',inspect),inspect,construction};
