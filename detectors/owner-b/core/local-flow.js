'use strict';
const php = require('php-parser');
const parser = new php.Engine({parser:{phpVersion:'8.2',suppressErrors:false},ast:{withPositions:true}});
const LIMITS={max_source_bytes:262144,max_ast_nodes:20000,max_ast_depth:128,max_sql_bytes:8192};
/** @param {any} node @param {(node:any)=>void} visit */
function walk(node,visit) {
  const pending=[{value:node,depth:0}];let count=0;
  while(pending.length) {
    const {value,depth}=pending.pop();
    if(!value || typeof value!=='object')continue;
    if(depth>LIMITS.max_ast_depth)throw new Error('AST depth exceeds bound');
    if(value.kind) {if(++count>LIMITS.max_ast_nodes)throw new Error('AST node count exceeds bound');visit(value);}
    const children=Array.isArray(value)?value:Object.entries(value).filter(([k])=>!['loc','comments','leadingComments','trailingComments'].includes(k)).map(([,v])=>v);
    for(let i=children.length-1;i>=0;i--)pending.push({value:children[i],depth:depth+1});
  }
}
/** @param {string} text @param {string} path */
function parse(text,path) {
  if (Buffer.byteLength(text)>LIMITS.max_source_bytes) throw new Error('Source size exceeds bound');
  if (!text.trim().startsWith('<?php')) throw new Error('Only PHP source with opening tag supported');
  // Reject extreme nesting before recursive parsers can consume excessive stack/CPU.
  let depth=0;
  for (const token of text) { if ('({['.includes(token)) { if (++depth>LIMITS.max_ast_depth) throw new Error('Source nesting exceeds bound'); } else if (')}]'.includes(token)) depth=Math.max(0,depth-1); }
  const ast=parser.parseCode(text,path);
  let count=0;
  walk(ast,()=>{if (++count>LIMITS.max_ast_nodes) throw new Error('AST node count exceeds bound');});
  return ast;
}
/** @param {any} node */
function expression(node) { return node?.kind==='expressionstatement'?node.expression:node; }
/** @param {any} node */
function variable(node) {return node?.kind==='variable' && typeof node.name==='string'?node.name:null;}
/** @param {any} node @param {string} name */
function uses(node,name) {let found=false;walk(node,n=>{if(variable(n)===name)found=true;});return found;}
/** @param {any} node @param {Map<string,any>} definitions @returns {string|null} */
function literal(node,definitions) {
  if (node?.kind==='string') return node.value;
  if (node?.kind==='variable') return definitions.get(variable(node))?.literal ?? null;
  if (node?.kind==='bin' && node.type==='.') {
    const a=literal(node.left,definitions),b=literal(node.right,definitions);
    return a!==null && b!==null?a+b:null;
  }
  return null;
}
/** Find named scopes; closures and top-level executable source cannot certify whole-result flow.
 * @param {any} ast */
function scopes(ast) {
  const result=[];
  function visit(node,prefix='') {
    if (!node || typeof node!=='object') return;
    if (Array.isArray(node)) {for(const n of node)visit(n,prefix);return;}
    if (node.kind==='namespace') prefix=(node.name?.name||node.name||'')+'\\';
    if (node.kind==='class') prefix+=(node.name?.name||'anonymous-class')+'::';
    if (['function','method'].includes(node.kind)) {
      if(node.body)result.push({name:prefix+(node.name?.name||node.name),statements:node.body.children||[],node});
      return;
    }
    for(const [key,value]of Object.entries(node))if(key!=='loc')visit(value,prefix);
  }
  visit(ast);
  const executable=(ast.children||[]).filter(n=>!['function','class','namespace','usegroup','noop','declare'].includes(n.kind));
  if(executable.length)result.push({name:'<top-level>',statements:executable,node:ast});
  return result;
}
module.exports={parse,scopes,walk,expression,variable,uses,literal,LIMITS};
