'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {checks}=require('../index');
const {input}=require('./g01-fixtures');
test('DB-06 public registry validates and evaluates its check',()=>{
  const payload=input('DB-06');

  const result=checks['DB-06'].evaluate(payload);
  assert.equal(result.status,'completed');
  assert.equal(result.findings.length,1);
});
