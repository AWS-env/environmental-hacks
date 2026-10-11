'use strict';
// Static ORM / SQL engine for Python, JavaScript and TypeScript sources (Owner B, rule_version orm-1).
// Mirrors core/jobs.js: one adapter evaluates the whole scope, every file is parsed (never executed),
// a file that cannot be parsed or is an unsupported language is reported as a limitation, never as clean.
const {validate, validatePair, envelope, fingerprint, splitLines} = require('./contract');
const {parse, languageOf} = require('../parsers/tree-sitter');

const VERSION = '1.0.0';
const RULE_VERSION = 'orm-1';
const MAX_EVIDENCE_LINES = 4;
// A hostile or generated file can hold thousands of matches; keep the result bounded and say so instead of truncating silently.
const MAX_FINDINGS_PER_FILE = 200;
const splitCache = new WeakMap();

function requireValue(ok, message) {if (!ok) throw new Error(message);}
/** Exact source lines [start, end] (1-based) as a contract evidence item, capped. The split is cached per source (O(n), not O(n x findings)). */
function line(source, start, end = start) {
  let lines = splitCache.get(source);
  if (!lines) {lines = splitLines(source.content); splitCache.set(source, lines);}
  const last = Math.min(end, start + MAX_EVIDENCE_LINES - 1, lines.length);
  return {source_id: source.source_id, kind: 'static', locator: source.locator, line_start: start, value: lines.slice(start - 1, last).join('\n')};
}
/** Source-line evidence for a tree-sitter node. */
function nodeLines(source, node) {return line(source, node.startPosition.row + 1, node.endPosition.row + 1);}
function finding(identity, summary, recommendation, evidence, references, confidence = 'medium') {
  return {identity, summary, recommendation, evidence, references, confidence};
}
/** Name of the enclosing class/function chain, e.g. "Repo.list_users"; "<module>" at top level. */
function enclosing(node) {
  const names = [];
  for (let n = node.parent; n; n = n.parent) {
    let name = null;
    if (['function_definition', 'class_definition', 'function_declaration', 'class_declaration', 'method_definition', 'generator_function_declaration'].includes(n.type)) name = n.childForFieldName('name')?.text;
    else if (['arrow_function', 'function_expression', 'function'].includes(n.type) && n.parent?.type === 'variable_declarator') name = n.parent.childForFieldName('name')?.text;
    if (name) names.unshift(name);
  }
  return names.join('.') || '<module>';
}
/** Depth-first walk over named nodes in source order. */
function walk(node, visit) {
  visit(node);
  for (const child of node.namedChildren) walk(child, visit);
}
/** Number of the literal an expression node denotes, or null when it is not an integer literal. */
function integerLiteral(node) {
  if (!node) return null;
  const t = node.text.replace(/_/g, '');
  if (['integer', 'number'].includes(node.type) && /^\d+$/.test(t)) return Number(t);
  if (node.type === 'parenthesized_expression' && node.namedChildCount === 1) return integerLiteral(node.namedChildren[0]);
  return null;
}
/** String-literal body: strip the prefix and exactly the opening/closing delimiter (so a SQL literal's own quotes survive). */
function sqlOf(node) {
  const m = /^[rbfuRBFU]*('''|"""|'|"|`)/.exec(node.text);
  return m && node.text.endsWith(m[1]) ? node.text.slice(m[0].length, node.text.length - m[1].length) : node.text;
}
/** Methods called along a call chain, root first, plus the root node and the call nodes. */
function chainOf(node, lang) {
  const callType = lang === 'python' ? 'call' : 'call_expression', memberType = lang === 'python' ? 'attribute' : 'member_expression';
  const calls = [];
  let cur = node;
  while (cur && cur.type === callType) {
    const fn = cur.childForFieldName('function');
    calls.unshift({method: fn?.type === memberType ? fn.childForFieldName(lang === 'python' ? 'attribute' : 'property')?.text : fn?.text, node: cur});
    cur = fn?.type === memberType ? fn.childForFieldName('object') : null;
  }
  return {calls, methods: calls.map(c => c.method), root: cur};
}
/** Driver / ORM methods whose first argument is raw SQL. */
const EXEC_METHODS = new Set(['execute', 'query', 'raw', 'fetch', 'fetchall', 'fetchrow', 'exec']);

/** inspect(file) -> candidates [{identity, summary, recommendation, evidence, references, confidence}].
 * Each file is evaluated independently; the identity must be a semantic anchor without line numbers. */
async function evaluateOrm(input, key, inspect) {
  validate(input);
  requireValue(input.kind === 'input' && input.check_id === key, 'Incorrect check dispatch');
  const result = envelope(input);
  for (const scope of input.scope) {
    try {
      requireValue(input.detector_version === VERSION && input.context.rule_version === RULE_VERSION, 'Unsupported detector/rule version');
      requireValue(input.context.mode === 'candidate', 'Require explicit candidate mode');
      const sources = input.sources.filter(s => s.scope_id === scope);
      requireValue(sources.length === 1 && sources[0].kind === 'static', 'Require exactly one static source per file scope');
      const source = sources[0], lang = languageOf(source.locator);
      requireValue(lang, 'Unsupported file type (Python, JavaScript and TypeScript only)');
      const tree = await parse(lang, source.content);
      let candidates;
      try {candidates = inspect({source, lang, root: tree.rootNode, input});} finally {tree.delete();}
      const seen = new Map();
      if (candidates.length > MAX_FINDINGS_PER_FILE) {
        result.coverage.limitations.push(`${scope}: ${candidates.length - MAX_FINDINGS_PER_FILE} further candidate(s) not listed (cap ${MAX_FINDINGS_PER_FILE} per file)`);
        candidates = candidates.slice(0, MAX_FINDINGS_PER_FILE);
      }
      const findings = candidates.map(c => {
        const n = (seen.get(c.identity) || 0) + 1;
        seen.set(c.identity, n);
        const identity = n === 1 ? c.identity : `${c.identity}#${n}`;
        return {...c, identity, scope_id: scope, fingerprint: fingerprint(input.repository_id, key, scope, identity)};
      });
      result.findings.push(...findings);
      result.coverage.evaluated_scope.push(scope);
    } catch (e) {result.coverage.limitations.push(`${scope}: ${e.message}`);}
  }
  const evaluated = result.coverage.evaluated_scope.length;
  result.status = evaluated === input.scope.length ? 'completed' : evaluated ? 'partial' : 'unavailable';
  result.coverage.limitations.push('Static syntax-tree analysis of supplied source only; no client code is executed. Findings are candidates: table size and runtime cost are unknown. No inferred CPU, energy or carbon savings.');
  validatePair(input, result);
  return result;
}
module.exports = {VERSION, RULE_VERSION, requireValue, line, nodeLines, finding, enclosing, walk, integerLiteral, sqlOf, chainOf, EXEC_METHODS, evaluateOrm};
