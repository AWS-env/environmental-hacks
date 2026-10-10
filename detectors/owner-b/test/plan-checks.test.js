'use strict';
// DB-43 / DB-45 / DB-46 against REAL PostgreSQL 16.15 plans (fixtures/explain/pg16-*.json, captured from a local throwaway
// container with EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)). Cases: positive, similar negative, legitimate exception, missing
// evidence, malformed input, boundary, plus a mutation check (always-empty and always-flag detectors must fail some case).
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {evaluate} = require('../core/dispatch'), {validatePair} = require('../core/contract');
const plans = require('../core/plans');

const DIR = path.join(__dirname, 'fixtures', 'explain');
const fixture = name => JSON.parse(fs.readFileSync(path.join(DIR, `pg16-${name}.json`), 'utf8'));
const CONTEXTS = {
  'DB-43': {min_rows_examined: 10000, min_removed_ratio: 0.9},
};
/** Contract-v1 input with one artifact source per query. */
function build(check, queries, ctx = {}) {
  const scope = queries.map(q => `query:${q.id}`);
  return {schema_version: '1.0', kind: 'input', repository_id: 'github:test/plans', scan_id: 'scan-1', commit_sha: 'a'.repeat(40), check_id: check, detector_version: '1.0.0',
    context: {rule_version: 'plan-1', mode: 'candidate', ...CONTEXTS[check], ...ctx}, scope,
    sources: queries.filter(q => q.data !== null).map(q => ({source_id: `src-${q.id}`, scope_id: `query:${q.id}`, kind: 'artifact', locator: q.locator ?? `app/repo.py:${q.id}`, data: q.data}))};
}
const artifact = (name, patch = {}) => {
  const plan = fixture(name);
  return {format: 'explain-plan-v1', acquisition: {status: 'complete'}, dialect: 'postgresql-16', analyzed: plan[0].Plan['Actual Loops'] !== undefined,
    sql: 'SELECT * FROM orders WHERE status = $1', plan, ...patch};
};
const ids = r => r.findings.map(f => f.identity).sort();

const CASES = [
  // DB-43
  {check: 'DB-43', name: 'positive: seq scan discarding 199,960 of 200,000 rows', q: [{id: 1, data: artifact('seqscan-filter')}], identities: ['seq-scan:orders']},
  {check: 'DB-43', name: 'negative: primary-key index lookup', q: [{id: 1, data: artifact('index-lookup')}], identities: []},
  {check: 'DB-43', name: 'exception: tiny 3-row table seq scan', q: [{id: 1, data: artifact('seqscan-small')}], identities: []},
  {check: 'DB-43', name: 'boundary: rows examined exactly at the floor is flagged', q: [{id: 1, data: artifact('seqscan-filter')}], ctx: {min_rows_examined: 200000}, identities: ['seq-scan:orders']},
  {check: 'DB-43', name: 'boundary: one row above the floor is exempt', q: [{id: 1, data: artifact('seqscan-filter')}], ctx: {min_rows_examined: 200001}, identities: []},
  {check: 'DB-43', name: 'exception: a scan that keeps most rows (ratio floor above the removed share)', q: [{id: 1, data: artifact('seqscan-filter')}], ctx: {min_removed_ratio: 0.99999}, identities: []},
  {check: 'DB-43', name: 'missing: plain EXPLAIN without ANALYZE is not evaluated', q: [{id: 1, data: artifact('plain-seqscan')}], identities: [], status: 'unavailable'},
  {check: 'DB-43', name: 'missing: no artifact for the scope', q: [{id: 1, data: null}], identities: [], status: 'unavailable'},
  {check: 'DB-43', name: 'malformed: plan is not an EXPLAIN JSON array', q: [{id: 1, data: artifact('seqscan-filter', {plan: {Plan: {}}})}], identities: [], status: 'unavailable'},
  {check: 'DB-43', name: 'malformed: unsupported dialect', q: [{id: 1, data: artifact('seqscan-filter', {dialect: 'mysql-8.0'})}], identities: [], status: 'unavailable'},
  {check: 'DB-43', name: 'missing: acquisition incomplete', q: [{id: 1, data: artifact('seqscan-filter', {acquisition: {status: 'unavailable', reason: 'timeout'}})}], identities: [], status: 'unavailable'},
  {check: 'DB-43', name: 'partial: one good and one malformed query', q: [{id: 1, data: artifact('seqscan-filter')}, {id: 2, data: artifact('seqscan-filter', {format: 'other'})}], identities: ['seq-scan:orders'], status: 'partial'},
];
CASES.forEach((c, i) => {
  test(`${c.check}-${String(i + 1).padStart(2, '0')} ${c.name}`, async () => {
    const input = build(c.check, c.q, c.ctx), r = await evaluate(input);
    assert.equal(r.status, c.status || 'completed');
    assert.deepEqual(ids(r), [...c.identities].sort());
    assert.deepEqual(r.measurements, []);
    if (r.status !== 'completed') assert.ok(r.coverage.limitations.length > 0);
    validatePair(input, r);
    for (const f of r.findings) assert.ok(f.evidence.every(e => e.kind === 'artifact' && e.field));
  });
});
for (const check of Object.keys(CONTEXTS)) {
  const positive = CASES.find(c => c.check === check && c.identities.length && !c.ctx);
  test(`${check} rejects a context without its thresholds (no silent default)`, async () => {
    const input = build(check, positive.q);
    for (const key of Object.keys(CONTEXTS[check])) delete input.context[key];
    assert.equal((await evaluate(input)).status, 'unavailable');
  });
  test(`${check} fingerprints are stable when unrelated plan numbers change`, async () => {
    const a = await evaluate(build(check, positive.q));
    const changed = JSON.parse(JSON.stringify(positive.q));
    for (const q of changed) q.data.plan[0]['Execution Time'] += 1;
    const b = await evaluate(build(check, changed));
    assert.deepEqual(a.findings.map(f => f.fingerprint), b.findings.map(f => f.fingerprint));
  });
  test(`${check} mutation check: always-empty and always-flag detectors fail some committed case`, async () => {
    const mine = CASES.filter(c => c.check === check);
    const runWith = async inspect => {
      let failed = 0;
      for (const c of mine) {
        const r = await plans.evaluatePlans(build(check, c.q, c.ctx), check, inspect);
        if (r.status !== (c.status || 'completed') || JSON.stringify(ids(r)) !== JSON.stringify([...c.identities].sort())) failed++;
      }
      return failed;
    };
    const real = require(`../checks/${check.toLowerCase()}`).inspect;
    assert.equal(await runWith(real), 0);
    assert.ok(await runWith(() => []) > 0, 'always-empty must fail');
    assert.ok(await runWith(({source}) => [plans.finding('mutant', 'mutant', 'mutant', [plans.cite(source, 'sql')], ['https://example.com/x'])]) > 0, 'always-flag must fail');
  });
}
