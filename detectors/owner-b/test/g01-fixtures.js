'use strict';
const crypto=require('node:crypto');
const {LIMITS}=require('../core/local-flow');
const context={language:'php',php_version:'8.2',rule_version:'g01-1',mode:'runtime',sql_dialect:'mysql-8.0',ordinary_tables:['users','articles'],db_apis:{pdo:{family:'pdo',version:'8.2'},db:{family:'joomla',version:'4.4'}},cache_apis:{cache:{family:'psr16',version:'3.0'},mem:{family:'memcached',version:'3.2'},pool:{family:'psr6',version:'3.0'}},limits:LIMITS};
const positives={
  'DB-34':'<?php\nfunction load($pdo, $cache) {\n  $sql = "SELECT id FROM users";\n  $cached = $cache->get("users");\n  if ($cached !== null) { return $cached; }\n  return $pdo->query($sql);\n}\n',
  'DB-06':'<?php\nfunction load($pdo) {\n  $sql = "SELECT id FROM users WHERE 1=0";\n  return $pdo->query($sql);\n}\n',
  'DB-05':'<?php\nfunction load($pdo) {\n  $data = $pdo->query("SELECT id FROM users");\n  $data = $pdo->query("SELECT id FROM articles");\n  return $data;\n}\n',
  'DB-16':'<?php\nfunction load($pdo) {\n  $pdo->query("SELECT id FROM users");\n  return 7;\n}\n'
};
/** @param {string} check @param {string} [code] @returns {any} */
function input(check,code=positives[check]) {
  return {schema_version:'1.0',kind:'input',repository_id:'synthetic:g01-fixtures',scan_id:'synthetic-before',commit_sha:'a'.repeat(40),check_id:check,detector_version:'1.0.0',context:JSON.parse(JSON.stringify(context)),scope:['file:fixture.php'],sources:[{source_id:'php',scope_id:'file:fixture.php',kind:'static',locator:'fixture.php',content:code}]};
}
/** Synthetic fixtures prove correlation logic only; they are never real acquisition receipts.
 * @param {any} payload @param {string[]} [sqls] */
function withEvents(payload,sqls=['SELECT id FROM users','SELECT id FROM articles']) {
  const source=payload.sources[0];
  payload.sources.push({source_id:'events',scope_id:source.scope_id,kind:'telemetry',locator:'synthetic:query-events',data:{format:'query-event-v1',acquisition:{status:'complete',start:'2026-10-09T00:00:00Z',end:'2026-10-09T00:01:00Z'},events:sqls.map((sql,i)=>({event_id:'synthetic-event-'+i,request_id:'synthetic-request',transaction_id:'synthetic-transaction',repository_id:payload.repository_id,commit_sha:payload.commit_sha,source_sha256:crypto.createHash('sha256').update(source.content).digest('hex'),timestamp:`2026-10-09T00:00:0${i+1}Z`,function:'load',operation_id:String(i+1),locator:source.locator,sql,status:'success',dialect:payload.context.sql_dialect}))}});
  return payload;
}
module.exports={input,withEvents,positives,context};
