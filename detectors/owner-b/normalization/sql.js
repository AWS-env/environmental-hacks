'use strict';
const {Parser}=require('node-sql-parser');
const {LIMITS}=require('../core/local-flow');
const parser=new Parser();
const DIALECTS={'mysql-8.0':'MySQL','postgresql-16':'Postgresql'};
/** Predicate results use SQL UNKNOWN (null), and unsupported expressions (undefined).
 * @param {any} node @returns {boolean|null|undefined} */
function predicate(node) {
  if(!node)return undefined;
  if(node.type==='bool')return Boolean(node.value);
  if(node.type==='binary_expr') {
    const op=node.operator.toUpperCase();
    if(op==='AND'||op==='OR') {
      const a=predicate(node.left),b=predicate(node.right);
      if(a===undefined||b===undefined)return undefined;
      if(op==='AND')return a===false||b===false?false:a===null||b===null?null:true;
      return a===true||b===true?true:a===null||b===null?null:false;
    }
    const a=scalar(node.left),b=scalar(node.right);
    if(a===undefined||b===undefined)return undefined;
    if(op==='IS'||op==='IS NOT') {
      if(b!==null)return undefined;
      return op==='IS'?a===null:a!==null;
    }
    if(!['=','!=','<>','<','>','<=','>='].includes(op))return undefined;
    if(a===null||b===null)return null;
    if(typeof a!==typeof b || typeof a!=='number')return undefined;
    return {'=':a===b,'!=':a!==b,'<>':a!==b,'<':a<b,'>':a>b,'<=':a<=b,'>=':a>=b}[op];
  }
  return undefined;
}
/** @param {any} node @returns {number|null|undefined} */
function scalar(node) {
  if(node?.type==='null')return null;
  if(node?.type==='number' && Number.isSafeInteger(node.value))return node.value;
  return undefined;
}
/** @param {any} value @param {(node:any)=>boolean} reject */
function any(value,reject) {
  if(!value||typeof value!=='object')return false;
  if(reject(value))return true;
  return Object.values(value).some(v=>any(v,reject));
}
/** @param {string} text @param {any} context */
function analyzeSql(text,context) {
  if(!DIALECTS[context.sql_dialect])throw new Error('Unsupported SQL dialect/version');
  if(Buffer.byteLength(text)>LIMITS.max_sql_bytes)throw new Error('SQL size exceeds bound');
  let nesting=0;for(const char of text) {if(char==='(' && ++nesting>128)throw new Error('SQL nesting exceeds bound');if(char===')')nesting=Math.max(0,nesting-1);}
  /** Runtime AST includes dialect fields missing from the library's public union. @type {any} */
  let ast;
  try {ast=parser.astify(text,{database:DIALECTS[context.sql_dialect]});}catch {throw new Error('Unsupported or malformed SQL');}
  if(Array.isArray(ast)) {if(ast.length!==1)throw new Error('Multiple SQL statements unsupported');ast=ast[0];}
  if(ast.type!=='select')return {safe:false,knownEmpty:false,reason:'Non-read statement'};
  // Unknown functions/CTEs/views/joins/aggregation can have side effects or alter empty-result semantics.
  const tables=ast.from||[];
  if(tables.some(t=>t.table&&!context.ordinary_tables.includes(t.table)))throw new Error('Missing ordinary-table identity metadata');
  const simple=tables.length<=1 && tables.every(t=>t.table && !t.expr && !t.join && !t.db && context.ordinary_tables.includes(t.table));
  const forbidden=ast.with||ast.into?.position||ast.locking_read||ast._next||ast.groupby||ast.having||ast.window||ast.options?.length||ast.union;
  const expressionEffect=any(ast,n=>['function','aggr_func','select','var','assign'].includes(n.type) && n!==ast);
  if(!simple || forbidden || expressionEffect)return {safe:false,knownEmpty:false,reason:'Not a declared ordinary side-effect-free SELECT'};
  // MySQL executable comments and output/locking/session clauses are conservative exclusions.
  if(/\/\*!|\b(FOR\s+(UPDATE|SHARE)|LOCK\s+IN|INTO|OUTFILE|SQL_CALC_FOUND_ROWS)\b/i.test(text))return {safe:false,knownEmpty:false,reason:'Lock/output/session semantics'};
  const truth=predicate(ast.where);
  return {safe:true,knownEmpty:truth===false||truth===null,truth,ast};
}
module.exports={analyzeSql,predicate,DIALECTS};
