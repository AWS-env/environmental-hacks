'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {checks}=require('../index');
const {input,withEvents}=require('./g01-fixtures');
test('DB-05 public registry validates and evaluates its check',()=>{
  const payload=input('DB-05');
  withEvents(payload);
  const result=checks['DB-05'].evaluate(payload);
  assert.equal(result.status,'completed');
  assert.equal(result.findings.length,1);
});
