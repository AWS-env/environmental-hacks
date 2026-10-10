'use strict';
// Loop helpers shared by the static ORM checks that look for per-item database calls inside loops (DB-13 writes, DB-09 reads).
const orm = require('./orm');

// Receivers are recognised by name; renamed handles are missed (documented limitation of every check that uses them).
const PY_DB_RECEIVER = /^(self\.)?(db|session|sess|conn|connection|cursor|cur)(\.session)?$/i;
const JS_DB_RECEIVER = /\b(prisma|prismaClient|db|dbRead|dbWrite|tx|trx|knex|repo|repository|manager|entityManager|em|dataSource|sequelize)\b|(Repository|Repo|Model)\b/;
// One-off scripts (seed, migration, management commands, fixtures) rarely sit on a request path: keep the finding, lower the confidence.
const ONE_OFF_PATH = /(^|\/)(management\/commands|migrations?|migration-scripts|scripts?|seeds?|fixtures?|__fixtures__|integration-tests|demo|cli)(\/|$)|(random_data|populate|seed|fixture)[^/]*$/i;
const DML = /^\s*(insert|update|delete|replace|merge)\b/i;
const CALLBACK_LOOPS = new Set(['forEach', 'map', 'flatMap', 'filter', 'some', 'every', 'reduce', 'each', 'eachSeries', 'mapSeries']);
const FUNCTIONS = new Set(['function_definition', 'function_declaration', 'method_definition', 'class_definition', 'class_declaration', 'generator_function_declaration']);
const COMPREHENSIONS = new Set(['list_comprehension', 'set_comprehension', 'generator_expression', 'dictionary_comprehension']);

/** Identifier names bound by (or used in) a node. */
function names(node) {
  const out = new Set();
  if (!node) return out;
  orm.walk(node, n => {if (['identifier', 'shorthand_property_identifier_pattern'].includes(n.type)) out.add(n.text);});
  return out;
}
/** @returns {{kind:string, header:any, bound:Set<string>, iterable:any, body:any}|null} */
function loopOf(node, lang) {
  if (lang === 'python') {
    if (node.type === 'for_statement') return {kind: 'for', header: node, bound: names(node.childForFieldName('left')), iterable: node.childForFieldName('right'), body: node.childForFieldName('body')};
    if (COMPREHENSIONS.has(node.type)) {
      const clauses = node.namedChildren.filter(c => c.type === 'for_in_clause');
      if (!clauses.length) return null;
      const bound = new Set(clauses.flatMap(c => [...names(c.childForFieldName('left'))]));
      return {kind: 'comprehension', header: node, bound, iterable: clauses[0].childForFieldName('right'), body: node};
    }
    return null;
  }
  if (node.type === 'for_in_statement') return {kind: 'for-of', header: node, bound: names(node.childForFieldName('left')), iterable: node.childForFieldName('right'), body: node.childForFieldName('body')};
  if (node.type === 'for_statement') return {kind: 'for', header: node, bound: names(node.childForFieldName('initializer')), iterable: null, body: node.childForFieldName('body')};
  if (node.type === 'call_expression') {
    const fn = node.childForFieldName('function'), args = node.childForFieldName('arguments');
    const method = fn?.type === 'member_expression' ? fn.childForFieldName('property')?.text : null;
    const cb = args?.namedChildren[0];
    if (method && CALLBACK_LOOPS.has(method) && cb && ['arrow_function', 'function_expression', 'function'].includes(cb.type)) {
      const params = cb.childForFieldName('parameters') ?? cb.childForFieldName('parameter');
      const list = params?.type === 'identifier' ? [params] : params?.namedChildren ?? [];
      const used = method === 'reduce' ? list.slice(1) : list.slice(0, 1);
      return {kind: method, header: node, bound: new Set(used.flatMap(p => [...names(p)])), iterable: fn.childForFieldName('object'), body: cb};
    }
  }
  return null;
}
/** Names that carry the loop variable: the bound names plus names assigned inside the body from an expression using them. */
function tainted(loop) {
  const set = new Set(loop.bound);
  for (let pass = 0; pass < 3; pass++) {
    orm.walk(loop.body, n => {
      let target = null, value = null;
      if (n.type === 'assignment' || n.type === 'augmented_assignment') {target = n.childForFieldName('left'); value = n.childForFieldName('right');}
      else if (n.type === 'variable_declarator') {target = n.childForFieldName('name'); value = n.childForFieldName('value');}
      if (target && value && [...names(value)].some(x => set.has(x))) for (const x of names(target)) set.add(x);
    });
  }
  return set;
}
/** Loops that step by more than one (`i += batchSize`, `range(0, n, step)`) already process a batch per iteration. */
function steppedLoop(loop) {
  if (loop.header.type === 'for_statement' && loop.header.childForFieldName('increment')?.type.startsWith('augmented_assignment')) return true;
  const it = loop.iterable;
  if (it?.type === 'call' && it.childForFieldName('function')?.text === 'range') {
    const a = it.childForFieldName('arguments')?.namedChildren ?? [];
    return a.length === 3 && orm.integerLiteral(a[2]) !== 1;
  }
  return false;
}
/** A loop over a literal collection or range() of at most `max` items. */
function smallLiteral(loop, max) {
  const it = loop.iterable;
  if (!it) return false;
  if (['list', 'tuple', 'array'].includes(it.type)) return it.namedChildCount <= max;
  if (it.type === 'call' && it.childForFieldName('function')?.text === 'range') {
    const a = it.childForFieldName('arguments')?.namedChildren ?? [];
    const last = orm.integerLiteral(a[a.length === 1 ? 0 : 1]);
    return a.length >= 1 && a.every(x => orm.integerLiteral(x) !== null) && last !== null && last <= max;
  }
  return false;
}
function contains(outer, inner) {
  for (let n = inner; n; n = n.parent) if (n.id === outer.id) return true;
  return false;
}
/** Innermost loop around `node` that stays inside the same named function. */
function enclosingLoop(node, lang) {
  for (let n = node.parent; n && !FUNCTIONS.has(n.type); n = n.parent) {
    const l = loopOf(n, lang);
    if (l?.body && contains(l.body, node)) return l;
  }
  return null;
}
/** Text of the first argument when it is a string literal, or the leftmost literal of a "..." + x concatenation. */
function firstArgString(args) {
  let a = args?.namedChildren[0];
  while (a && ['binary_expression', 'binary_operator', 'parenthesized_expression'].includes(a.type)) a = a.childForFieldName('left') ?? a.namedChildren[0];
  return a && ['string', 'template_string'].includes(a.type) ? a.text.replace(/^[rbfu]*['"`]+/i, '') : null;
}
module.exports = {PY_DB_RECEIVER, JS_DB_RECEIVER, ONE_OFF_PATH, DML, names, loopOf, tainted, steppedLoop, smallLiteral, contains, enclosingLoop, firstArgString};
