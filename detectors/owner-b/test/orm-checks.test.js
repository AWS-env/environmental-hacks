'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const {evaluate} = require('../core/dispatch'), {validatePair} = require('../core/contract');
const {cases, contexts, buildInput} = require('./orm-cases');

for (const [check, list] of Object.entries(cases)) {
  for (const c of list) {
    test(`${check}-${String(c.id).padStart(2, '0')} ${c.kind}: ${c.name}`, async () => {
      const i = buildInput(check, c), r = await evaluate(i);
      assert.equal(r.status, c.status || 'completed');
      assert.deepEqual(r.findings.map(f => f.identity).sort(), [...c.identities].sort());
      assert.deepEqual(r.measurements, []);
      if (c.confidence) assert.ok(r.findings.every(f => f.confidence === c.confidence));
      if (c.status === 'unavailable') assert.ok(r.coverage.limitations.length > 0);
      validatePair(i, r);
      for (const f of r.findings) assert.ok(f.evidence.every(e => e.kind === 'static' && e.line_start >= 1));
    });
  }
}
for (const check of Object.keys(contexts)) {
  const positive = cases[check].find(c => c.kind === 'positive' && c.identities.length);
  test(`${check} rejects a context without its threshold (no silent default)`, async () => {
    const i = buildInput(check, positive);
    for (const key of Object.keys(contexts[check])) delete i.context[key];
    assert.equal((await evaluate(i)).status, 'unavailable');
  });
  test(`${check} fingerprints ignore line numbers`, async () => {
    const a = await evaluate(buildInput(check, positive)), b = await evaluate(buildInput(check, {...positive, content: '\n\n\n' + positive.content}));
    assert.deepEqual(a.findings.map(f => f.fingerprint).sort(), b.findings.map(f => f.fingerprint).sort());
  });
}
// Hostile input (found by the adversarial review): a 176 KB string of repeated `where` cost DB-23 44 seconds (quadratic regex), and
// 5,000 findings in one file cost seconds (the source was re-split per finding). Both are bounded now.
test('DB-23 does not go quadratic on a hostile SQL string and ignores SQL beyond the length bound', {skip: !contexts['DB-23']}, async () => {
  const content = 'cur.execute("SELECT * FROM t ' + 'where '.repeat(30000) + '")\n';
  const started = Date.now();
  const r = await evaluate(buildInput('DB-23', {path: 'a.py', content}));
  assert.equal(r.status, 'completed');
  assert.deepEqual(r.findings, [], 'SQL over the length bound is not analysed');
  assert.ok(Date.now() - started < 5000, 'must stay far below the Lambda timeout');
});
test('findings per file are capped and the cap is reported as a limitation', async () => {
  const content = 'knex("t").offset(n);\n'.repeat(300);
  const i = buildInput('DB-39', {path: 'a.js', content}), r = await evaluate(i);
  assert.equal(r.findings.length, 200);
  assert.ok(r.coverage.limitations.some(l => /100 further candidate\(s\) not listed/.test(l)), r.coverage.limitations.join(' | '));
  validatePair(i, r);
});
// A regex written as "\b" inside a normal JS string silently becomes a backspace character and disables the word boundary.
test('detector sources contain no backspace control characters', () => {
  const fs = require('node:fs'), path = require('node:path');
  const root = path.join(__dirname, '..');
  const bad = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {if (!['node_modules', 'grammars', 'fixtures'].includes(e.name)) walk(p);}
      else if (e.name.endsWith('.js') && fs.readFileSync(p, 'utf8').includes(String.fromCharCode(8))) bad.push(p);
    }
  })(root);
  assert.deepEqual(bad, []);
});
