'use strict';
// The JS client collector without a database: safety rules, dedupe, artifact shape, and the full path
// collector -> contract input -> detector, using the REAL PostgreSQL 16 plans in fixtures/explain.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {ExplainCollector, attachPrisma, isExplainable, CHECK_CONTEXTS} = require('../client-collectors/js/owner-b-explain');
const {evaluate} = require('../core/dispatch'), {validatePair} = require('../core/contract');

const plan = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'explain', `pg16-${name}.json`), 'utf8'));
const SHA = 'a'.repeat(40);

test('only plain reads are explainable', () => {
  for (const ok of ['SELECT 1', ' select * from t where a = $1', 'WITH x AS (SELECT 1) SELECT * FROM x', '(SELECT 1)']) assert.equal(isExplainable(ok), true, ok);
  for (const bad of ['UPDATE t SET a = 1', 'DELETE FROM t', 'INSERT INTO t VALUES (1)', 'SELECT * FROM t FOR UPDATE', 'SELECT * INTO u FROM t',
    'WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d', 'DROP TABLE t', 'SELECT 1; DROP TABLE t', 'EXPLAIN SELECT 1']) assert.equal(isExplainable(bad), false, bad);
  assert.equal(isExplainable("SELECT 'update me' FROM t"), true, 'keywords inside string literals are ignored');
});
test('records a SELECT once, skips writes and duplicates, keeps placeholders but not parameter values', async () => {
  const collector = new ExplainCollector(), seen = [];
  const explain = async (sql, params) => {seen.push([sql, params]); return plan('seqscan-filter');};
  assert.equal(await collector.record(explain, 'SELECT * FROM orders WHERE status = $1', ['refunded'], 'src/a.ts:1'), true);
  assert.equal(await collector.record(explain, 'SELECT *   FROM orders WHERE status = $1', ['paid'], 'src/a.ts:2'), false);
  assert.equal(await collector.record(explain, "UPDATE orders SET status = 'x'", [], 'src/a.ts:3'), false);
  assert.deepEqual(collector.skipped, {not_read_only: 1, duplicate: 1, limit: 0, failed: 0});
  assert.equal(seen.length, 1);
  assert.ok(seen[0][0].startsWith('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT'));
  const [input] = collector.contractInputs({repositoryId: 'github:t/t', commitSha: SHA});
  assert.equal(JSON.stringify(input).includes('refunded'), false, 'parameter values are never stored');
});
test('a failing EXPLAIN is skipped and never throws into the test run; the query limit is enforced', async () => {
  const collector = new ExplainCollector({maxQueries: 1});
  assert.equal(await collector.record(async () => {throw new Error('statement timeout');}, 'SELECT 1 FROM a', []), false);
  assert.equal(collector.skipped.failed, 1);
  assert.equal(await collector.record(async () => plan('index-lookup'), 'SELECT 1 FROM b', []), true);
  assert.equal(await collector.record(async () => plan('index-lookup'), 'SELECT 1 FROM c', []), false);
  assert.equal(collector.skipped.limit, 1);
});
test('commit SHA must be a full lowercase SHA', async () => {
  const collector = new ExplainCollector();
  await collector.record(async () => plan('index-lookup'), 'SELECT 1 FROM b', []);
  assert.throws(() => collector.contractInputs({repositoryId: 'github:t/t', commitSha: 'main'}), /40-character/);
});
test('collector output flows through the detectors: real plans -> contract inputs -> findings, valid pairs', async () => {
  const collector = new ExplainCollector();
  const real = {'SELECT * FROM orders WHERE status = $1': 'seqscan-filter', 'SELECT * FROM orders ORDER BY total': 'sort-spill-serial',
    'SELECT c.id FROM customers_noidx c JOIN customers_noidx d ON d.id = c.id WHERE c.id <= 300': 'nestloop-inner-seqscan', 'SELECT * FROM orders WHERE id = $1': 'index-lookup'};
  for (const [sql, fixture] of Object.entries(real)) await collector.record(async () => plan(fixture), sql, [], 'src/x.ts:1');
  const files = collector.writeContractInputs(fs.mkdtempSync(path.join(os.tmpdir(), 'explain-')), {repositoryId: 'github:t/t', commitSha: SHA});
  const checks = Object.keys(CHECK_CONTEXTS);
  assert.equal(files.length, checks.length);
  const found = {};
  for (const file of files) {
    const input = JSON.parse(fs.readFileSync(file, 'utf8')), result = await evaluate(input);
    validatePair(input, result);
    assert.equal(result.status, 'completed');
    found[input.check_id] = result.findings.map(f => f.identity).sort();
  }
  // the nested-loop plan's outer side is also a filtered seq scan (19,700 of 20,000 rows removed), so DB-43 reports it too
  const EXPECTED = {'DB-43': ['seq-scan:customers_noidx(c)', 'seq-scan:orders'], 'DB-45': ['sort-disk:total'], 'DB-46': ['nested-loop-inner-seq-scan:customers_noidx(d)']};
  assert.deepEqual(found, Object.fromEntries(checks.map(c => [c, EXPECTED[c]])));
});
test('Prisma adapter explains reads from query events inside a transaction that is rolled back, and skips writes', async () => {
  const calls = [];
  const handlers = {};
  const tx = {
    $executeRawUnsafe: async sql => {calls.push(['exec', sql]);},
    $queryRawUnsafe: async (sql, ...params) => {calls.push(['query', sql, params]); return [{'QUERY PLAN': plan('seqscan-filter')}];},
  };
  const prisma = {$on: (name, fn) => {handlers[name] = fn;}, $transaction: async fn => fn(tx)};
  const collector = attachPrisma(prisma, new ExplainCollector());
  handlers.query({query: 'SELECT "t"."id" FROM "t" WHERE "t"."a" = $1', params: '["x"]'});
  handlers.query({query: 'UPDATE "t" SET "a" = $1', params: '["x"]'});
  handlers.query({query: 'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT 1', params: '[]'});
  await collector.settle();
  assert.equal(collector.queries.size, 1);
  assert.deepEqual(collector.skipped, {not_read_only: 2, duplicate: 0, limit: 0, failed: 0});
  assert.equal(calls[0][0], 'exec');
  assert.match(calls[0][1], /^SET LOCAL statement_timeout = 5000$/);
  assert.deepEqual(calls[1][2], ['x'], 'parameters are passed to EXPLAIN but never stored');
});
