'use strict';

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { scanSource, scanFile } = require('../db-34-detector');

const FIXTURES_DIR = path.join(__dirname, 'fixtures');

test('DB-34 Detector - Positive: Joomla-style direct query before cache lookup (AP-34)', () => {
  const filePath = path.join(FIXTURES_DIR, 'positive_joomla_cache.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 1, 'Expected exactly 1 finding for Joomla positive fixture');
  const f = findings[0];

  assert.strictEqual(f.check_id, 'DB-34');
  assert.strictEqual(f.rule_id, 'R1');
  assert.strictEqual(f.affected_resource, 'CPU');
  assert.strictEqual(f.confidence, 'High');
  assert.strictEqual(f.evidence.query_variable, '$query');

  // Construction on line 8
  assert.strictEqual(f.line_number, 8);
  assert.strictEqual(f.locations.construction.line, 8);

  // Cache check on line 10
  assert.strictEqual(f.locations.cache_check.line, 10);

  // Two-stage DB setup vs execution call
  assert.ok(f.locations.db_setup, 'db_setup must be present for Joomla setQuery');
  assert.strictEqual(f.locations.db_setup.line, 13, 'Setup call setQuery must be line 13');
  assert.strictEqual(f.evidence.setup_api, '$db->setQuery($query)');

  assert.strictEqual(f.locations.db_execution.line, 14, 'Actual execution call loadObjectList must be line 14');
  assert.strictEqual(f.evidence.db_api, '$db->loadObjectList()');

  assert.strictEqual(f.recommendation, 'Check cache first; construct SQL only on the miss path.');
  assert.match(f.bypass_explanation, /wasting query-construction CPU cycles/);
  assert.match(f.reference, /Shao et al/);
});

test('DB-34 Detector - Positive: PDO query before cache check', () => {
  const filePath = path.join(FIXTURES_DIR, 'positive_pdo_user.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 1, 'Expected exactly 1 finding for PDO positive fixture');
  const f = findings[0];

  assert.strictEqual(f.check_id, 'DB-34');
  assert.strictEqual(f.rule_id, 'R1');
  assert.strictEqual(f.confidence, 'High');
  assert.strictEqual(f.evidence.query_variable, '$sql');
  assert.strictEqual(f.line_number, 6);
  assert.strictEqual(f.locations.construction.line, 6);
  assert.strictEqual(f.locations.cache_check.line, 7);
  assert.strictEqual(f.locations.db_execution.line, 11);
  assert.strictEqual(f.evidence.db_api, '$pdo->query($sql)');
  assert.strictEqual(f.locations.db_setup, undefined, 'Direct PDO calls have no separate db_setup');
});

test('DB-34 Detector - Regression 1: Cache-miss early return produces NO finding', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_cache_miss_return.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'Early return on cache miss must NOT produce a finding');
});

test('DB-34 Detector - Regression 2: Unrelated object get() method produces NO finding', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_unrelated_get.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'Calls like $request->get() must NOT be treated as cache lookups');
});

test('DB-34 Detector - Regression 3: SQL-variable reassignment before DB call produces NO finding', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_sql_reassignment.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'Reassigning SQL variable before DB execution must NOT produce a finding');
});

test('DB-34 Detector - Negative: Cache checked first (optimized pattern)', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_cache_first.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'No finding expected when cache is checked before SQL construction');
});

test('DB-34 Detector - Negative: Unconditional query execution (no cache bypass)', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_unconditional_execution.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'No finding expected when SQL is executed on every path');
});

test('DB-34 Detector - Negative: SQL escapes to audit helper / used elsewhere', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_sql_escapes.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'No finding expected when SQL is used for auditing or escapes to helper');
});

test('DB-34 Detector - Negative: Lazy ORM query builder', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_lazy_orm.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'No finding expected for ORM query builder calls');
});

test('DB-34 Detector - Negative: Comments and UI strings resembling SQL keywords', () => {
  const filePath = path.join(FIXTURES_DIR, 'negative_unrelated_code.php');
  const findings = scanFile(filePath);

  assert.strictEqual(findings.length, 0, 'No finding expected for comments or UI strings');
});

test('DB-34 Detector - Edge cases: empty, non-existent, and malformed inputs', () => {
  assert.deepStrictEqual(scanSource(''), []);
  assert.deepStrictEqual(scanSource('   '), []);
  assert.deepStrictEqual(scanSource('plain text not php'), []);
  assert.deepStrictEqual(scanFile('non_existent_file_path.php'), []);
});

test('DB-34 Detector - Determinism: identical findings on repeated scans', () => {
  const filePath = path.join(FIXTURES_DIR, 'positive_joomla_cache.php');
  const run1 = scanFile(filePath);
  const run2 = scanFile(filePath);

  assert.deepStrictEqual(run1, run2, 'Scans must be strictly deterministic');
});
