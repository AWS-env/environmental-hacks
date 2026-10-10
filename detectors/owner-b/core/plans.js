'use strict';
// Query-plan engine (Owner B, rule_version plan-1): evaluates client-produced EXPLAIN artifacts (format explain-plan-v1).
// One artifact source per query, scope `query:<id>`. The artifact is data only: we never connect to the client's database and
// never execute anything. A plan that cannot be evaluated (plain EXPLAIN without ANALYZE, unsupported dialect, malformed JSON)
// is reported as a limitation on its scope, never as clean.
//
// artifact data shape (explain-plan-v1):
//   { format: 'explain-plan-v1', acquisition: {status: 'complete'}, dialect: 'postgresql-16', analyzed: true,
//     locator: 'app/repo.py:42' (call site, informational), sql: 'SELECT ... $1', plan: <EXPLAIN (FORMAT JSON) output array> }
const {validate, validatePair, envelope, fingerprint} = require('./contract');

const VERSION = '1.0.0';
const RULE_VERSION = 'plan-1';
const FORMAT = 'explain-plan-v1';
const MAX_PLAN_NODES = 2000;
const MAX_PLAN_DEPTH = 64;

function requireValue(ok, message) {if (!ok) throw new Error(message);}
/** Evidence citing a top-level field of an artifact source (the cited value must equal the field in full). */
function cite(source, field) {return {source_id: source.source_id, kind: source.kind, locator: source.locator, field, value: source.data[field]};}
function finding(identity, summary, recommendation, evidence, references, confidence = 'medium') {
  return {identity, summary, recommendation, evidence, references, confidence};
}
/** Plan nodes in execution-tree order with their parent. Bounded in size and depth. */
function nodesOf(root) {
  const out = [];
  (function visit(node, parent, depth) {
    requireValue(depth <= MAX_PLAN_DEPTH, 'Plan tree too deep');
    requireValue(out.length < MAX_PLAN_NODES, 'Plan has too many nodes');
    requireValue(node && typeof node === 'object' && typeof node['Node Type'] === 'string', 'Malformed plan node');
    out.push({node, parent});
    for (const child of node.Plans ?? []) visit(child, node, depth + 1);
  })(root, null, 0);
  return out;
}
const isSeqScan = node => node['Node Type'] === 'Seq Scan' || node['Node Type'] === 'Parallel Seq Scan';
/** Rows the node examined in total: the plan reports per-loop averages, so multiply by Actual Loops. */
function rowsExamined(node) {
  const per = (node['Actual Rows'] ?? 0) + (node['Rows Removed by Filter'] ?? 0);
  return per * (node['Actual Loops'] ?? 1);
}
/** Stable anchor for a node: relation (and alias when different) plus its ordinal among nodes of the same type. */
function relationName(node) {
  const rel = node['Relation Name'] ?? node['Index Name'] ?? 'unknown';
  return node.Alias && node.Alias !== rel ? `${rel}(${node.Alias})` : rel;
}
function number(value, name, min = 0) {requireValue(typeof value === 'number' && Number.isFinite(value) && value >= min, `Missing/invalid context.${name}`); return value;}

/** inspect({source, data, plan, nodes, input}) -> candidates. */
async function evaluatePlans(input, key, inspect, {needsAnalyze = true} = {}) {
  validate(input);
  requireValue(input.kind === 'input' && input.check_id === key, 'Incorrect check dispatch');
  const result = envelope(input);
  for (const scope of input.scope) {
    try {
      requireValue(input.detector_version === VERSION && input.context.rule_version === RULE_VERSION, 'Unsupported detector/rule version');
      requireValue(input.context.mode === 'candidate', 'Require explicit candidate mode');
      const sources = input.sources.filter(s => s.scope_id === scope);
      requireValue(sources.length === 1 && sources[0].kind === 'artifact', 'Require exactly one artifact source per query scope');
      const source = sources[0], data = source.data;
      requireValue(data.format === FORMAT, `Unsupported artifact format (require ${FORMAT})`);
      requireValue(data.acquisition?.status === 'complete', 'Acquisition incomplete or unavailable: ' + (data.acquisition?.reason || 'no receipt'));
      requireValue(typeof data.dialect === 'string' && /^postgresql-\d+$/.test(data.dialect), 'Unsupported dialect (PostgreSQL EXPLAIN JSON only)');
      requireValue(typeof data.sql === 'string' && data.sql.length > 0, 'Missing sql');
      requireValue(Array.isArray(data.plan) && data.plan.length === 1 && data.plan[0]?.Plan, 'Malformed plan: expect EXPLAIN (FORMAT JSON) output');
      const nodes = nodesOf(data.plan[0].Plan);
      const analyzed = nodes.every(({node}) => node['Actual Loops'] !== undefined);
      requireValue(data.analyzed === analyzed, 'Artifact analyzed flag does not match the plan');
      requireValue(!needsAnalyze || analyzed, 'Plain EXPLAIN has no actual row counts, Rows Removed by Filter or Sort Method: not evaluated');
      const candidates = inspect({source, data, plan: data.plan[0], nodes, input});
      const seen = new Map();
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
  result.coverage.limitations.push('Client-produced EXPLAIN artifact only; the database and client code were not accessed. Plans from small CI databases can make sequential scans look cheap or expensive, so findings are candidates. No inferred CPU, energy or carbon savings.');
  validatePair(input, result);
  return result;
}
module.exports = {VERSION, RULE_VERSION, FORMAT, requireValue, cite, finding, nodesOf, isSeqScan, rowsExamined, relationName, number, evaluatePlans};
