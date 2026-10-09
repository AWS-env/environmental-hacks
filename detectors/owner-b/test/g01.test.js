'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {evaluate}=require('../core/g01');
const {validatePair,validate,fingerprint,compare}=require('../core/contract');
const {input,withEvents}=require('./g01-fixtures');
const {predicate}=require('../normalization/sql');
const localPython=path.join(process.cwd(),'.venv-g01',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const python=process.env.G01_PYTHON||(fs.existsSync(localPython)?localPython:'python');
function run(check,code,mode='runtime') {
  const p=input(check,code);p.context.mode=mode;
  if(['DB-05','DB-16'].includes(check)&&mode==='runtime')withEvents(p);
  const result=evaluate(p);validatePair(p,result);return {p,result};
}
for(const check of ['DB-34','DB-06','DB-05','DB-16']) {
  test(check+'-01 positive with exact full-line evidence',()=>{
    const {p,result}=run(check);
    assert.equal(result.status,'completed',JSON.stringify(result.coverage));
    assert.equal(result.findings.length,1);
    assert.equal(result.findings[0].fingerprint,fingerprint(p.repository_id,check,p.scope[0],result.findings[0].identity));
    assert.ok(result.findings[0].evidence[0].value.startsWith('  '));
    assert.deepEqual(result.measurements,[]);
    if(check==='DB-34')assert.ok(result.findings[0].evidence.some(e=>e.value.includes('$cache->get')), 'Cache lookup must be cited independently of the branch');
    if(['DB-05','DB-16'].includes(check))assert.equal(result.findings[0].evidence.at(-1).field,'events');
  });
  test(check+'-04 missing source/metadata yields unavailable',()=>{
    const p=input(check);p.sources=[];
    assert.equal(evaluate(p).status,'unavailable');
    const q=input(check);delete q.context.limits;
    assert.equal(evaluate(q).status,'unavailable');
    if(['DB-05','DB-16'].includes(check))assert.equal(evaluate(input(check)).status,'unavailable');
  });
  test(check+'-05 malformed PHP reports error',()=>{
    const r=run(check,'<?php function broken( {').result;
    assert.equal(r.status,'error');assert.deepEqual(r.findings,[]);assert.deepEqual(r.coverage.evaluated_scope,[]);
  });
  test(check+' determinism, line movement, and Python reference parity',()=>{
    const {p,result}=run(check);
    assert.deepEqual(evaluate(p),result);
    const moved=run(check,p.sources[0].content.replace('<?php','<?php\n\n')).result;
    assert.equal(moved.findings[0].fingerprint,result.findings[0].fingerprint);
    assert.equal(moved.findings[0].evidence[0].line_start,result.findings[0].evidence[0].line_start+2);
    const py=spawnSync(python,['-c','import json,sys; from shared.contracts.validation import validate_pair; p=json.load(sys.stdin); validate_pair(p["input"],p["result"])'],{input:JSON.stringify({input:p,result}),encoding:'utf8'});
    assert.equal(py.status,0,py.stderr);
  });
}
test('DB-34-02 cache-first has completed clean coverage',()=>{
  const code='<?php\nfunction load($pdo,$cache) {\n $v=$cache->get("users");\n if ($v !== null) { return $v; }\n $sql="SELECT id FROM users";\n return $pdo->query($sql);\n}';
  const r=run('DB-34',code).result;assert.equal(r.status,'completed');assert.deepEqual(r.findings,[]);
});
test('DB-34-03 SQL used purposefully for audit or cache key suppresses',()=>{
  for(const statement of ['audit($sql);','$key = $sql;']) {
    const p=input('DB-34');p.sources[0].content=p.sources[0].content.replace('  $cached',`  ${statement}\n  $cached`);
    const r=evaluate(p);assert.equal(r.status,'completed');assert.deepEqual(r.findings,[]);
  }
});
test('DB-34-04 unknown cache API and version are unavailable',()=>{
  const p=input('DB-34');p.context.cache_apis={};assert.equal(evaluate(p).status,'unavailable');
  const q=input('DB-34');q.context.cache_apis.cache.version='0.0';assert.equal(evaluate(q).status,'unavailable');
  const escaped=input('DB-34');escaped.sources[0].content=escaped.sources[0].content.replace('  if ($cached','  mutate($cached);\n  if ($cached');assert.equal(evaluate(escaped).status,'unavailable');
});
test('DB-34-06 miss return, overwrite, execution before lookup, setup-only',()=>{
  const code=input('DB-34').sources[0].content;
  for(const variant of [code.replace('!== null','=== null'),code.replace('  return $pdo->query','  $sql="SELECT id FROM articles";\n  return $pdo->query'),code.replace('  $cached','  $pdo->query($sql);\n  $cached'),code.replace('return $pdo->query($sql);','$db->setQuery($sql); return 7;'),code.replace('return $cached;','return audit($sql);')])assert.equal(run('DB-34',variant).result.findings.length,0);
});
test('DB-34 models Joomla setup separately and PSR6 isHit',()=>{
  const p=input('DB-34');p.sources[0].content=p.sources[0].content.replace('return $pdo->query($sql);','$db->setQuery($sql);\n  return $db->loadObjectList();');
  const r=evaluate(p);assert.equal(r.status,'completed');assert.equal(r.findings.length,1);assert.ok(r.findings[0].evidence.some(e=>e.value.includes('loadObjectList')));
  const q=input('DB-34');q.sources[0].content=q.sources[0].content.replace('$cache->get','$pool->getItem').replace('$cached !== null','$cached->isHit()');
  assert.equal(evaluate(q).findings.length,1);
  q.sources[0].content=q.sources[0].content.replace('$cached->isHit()','$cached');assert.equal(evaluate(q).status,'unavailable');
});
test('DB-06-02 runtime predicate is not known',()=>{
  const r=run('DB-06',input('DB-06').sources[0].content.replace('1=0','id=7')).result;
  assert.equal(r.status,'completed');assert.deepEqual(r.findings,[]);
});
for(const check of ['DB-06','DB-05','DB-16'])test(check+'-03 locking or volatile query suppressed',()=>{
  for(const sql of ['SELECT id FROM users FOR UPDATE','SELECT RAND() FROM users WHERE 1=0','SELECT COUNT(*) FROM users WHERE 1=0','UPDATE users SET id=7']) {
    const code=`<?php\nfunction load($pdo) {\n $x=$pdo->query("${sql}");\n $x=$pdo->query("SELECT id FROM articles");\n return $x;\n}`;
    const r=run(check,code,'static_candidate').result;assert.equal(r.status,'completed');assert.deepEqual(r.findings,[]);
  }
});
test('DB-06-05 unsupported dialect and SQL grammar are unavailable',()=>{
  const p=input('DB-06');p.context.sql_dialect='sqlite';assert.equal(evaluate(p).status,'unavailable');
  assert.equal(run('DB-06',input('DB-06').sources[0].content.replace('1=0','id IN ()')).result.status,'unavailable');
});
test('DB-06-06 SQL NULL/coercion, constant rebound, and separate function scope',()=>{
  assert.equal(run('DB-06',input('DB-06').sources[0].content.replace('1=0','NULL=1')).result.findings.length,1);
  assert.equal(run('DB-06',input('DB-06').sources[0].content.replace('1=0',"1='0'")).result.findings.length,0);
  const code='<?php\nfunction load($pdo) {\n $sql="SELECT id FROM users WHERE 1=0";\n $sql="SELECT id FROM users WHERE id=7";\n return $pdo->query($sql);\n}';
  assert.equal(run('DB-06',code).result.status,'unavailable');
  const other=input('DB-06').sources[0].content+'\nfunction other($pdo) { return $pdo->query("SELECT id FROM users WHERE id=7"); }';
  assert.equal(run('DB-06',other).result.findings.length,1);
  const tri={type:'binary_expr',operator:'OR',left:{type:'binary_expr',operator:'=',left:{type:'null'},right:{type:'number',value:1}},right:{type:'binary_expr',operator:'=',left:{type:'number',value:1},right:{type:'number',value:1}}};assert.equal(predicate(tri),true);
});
test('DB-05-02/06 use, alias and branch-dependent overwrite',()=>{
  for(const use of ['consume($data);','$alias=$data;']) {
    const p=input('DB-05');p.sources[0].content=p.sources[0].content.replace('  $data = $pdo->query("SELECT id FROM articles");',`  ${use}\n  $data = $pdo->query("SELECT id FROM articles");`);
    assert.equal(evaluate(withEvents(p)).findings.length,0);
  }
  const p=input('DB-05');p.sources[0].content=p.sources[0].content.replace('  $data = $pdo->query("SELECT id FROM articles");','  if ($flag) { $data = $pdo->query("SELECT id FROM articles"); }');
  assert.equal(evaluate(withEvents(p)).status,'unavailable');
});
test('DB-05-04/05 wrong request, commit, sequence, event format never confirms',()=>{
  for(const field of ['request_id','transaction_id','commit_sha','source_sha256']) {
    const p=withEvents(input('DB-05'));p.sources[1].data.events[1][field]='wrong';
    const r=evaluate(p);assert.equal(r.status,'unavailable');assert.deepEqual(r.findings,[]);
  }
  const p=withEvents(input('DB-05'));p.sources[1].data.events[1].timestamp=p.sources[1].data.events[0].timestamp;assert.equal(evaluate(p).status,'unavailable');
  const q=withEvents(input('DB-05'));delete q.sources[1].data.events[0].operation_id;assert.equal(evaluate(q).status,'unavailable');
});
test('DB-16-02/06 returned, escaped, closure, lazy builder',()=>{
  for(const code of ['<?php function load($pdo) { return $pdo->query("SELECT id FROM users"); }','<?php function load($pdo) { $x=$pdo->query("SELECT id FROM users"); consume($x); }','<?php function load($pdo) { $x=$pdo->query("SELECT id FROM users"); $f=function() use ($x) { return $x; }; }','<?php function load() { $x=Post::where("active",true); }'])assert.equal(run('DB-16',code).result.findings.length,0);
});
test('Hybrid source-only candidate mode declares lack of runtime confirmation',()=>{
  for(const check of ['DB-05','DB-16']) {
    const r=run(check,undefined,'static_candidate').result;assert.equal(r.status,'completed');assert.equal(r.findings.length,1);assert.match(r.findings[0].summary,/Static candidate/);assert.match(r.coverage.limitations.join(' '),/unverified|no runtime/i);
  }
});
test('Contract rejects fabricated citations, scopes, identity, duplicates and invalid SHA',()=>{
  const {p,result}=run('DB-34');
  for(const mutate of [r=>r.findings[0].evidence[0].value='invented',r=>r.findings[0].evidence[0].source_id='missing',r=>r.findings[0].evidence[0].line_start=999,r=>r.findings[0].scope_id='other',r=>r.scope=['other'],r=>r.commit_sha='abc',r=>r.findings.push(r.findings[0]),r=>r.context.mode='other']) {
    const r=JSON.parse(JSON.stringify(result));mutate(r);assert.throws(()=>validatePair(p,r));
  }
  const {p:q,result:s}=run('DB-05');s.findings[0].evidence.at(-1).value=[];assert.throws(()=>validatePair(q,s));
  assert.equal(fingerprint('github:AWS-env/example','OBS-01','file:config/production.yaml','production-log-level'),'22ff2e3a29b6270027d8f01268ac9e3683e44f707cbd979fb88af6b33e0c9aab');
});
test('Coverage: partial file failures and comparisons cannot certify a fix',()=>{
  const {p,result}=run('DB-34');p.scope.push('file:missing.php');
  const partial=evaluate(p);assert.equal(partial.status,'partial');assert.equal(partial.findings.length,1);
  const failed=evaluate({...p,sources:[]});assert.deepEqual(compare(result,failed).no_longer_detected,[]);
  const changed=JSON.parse(JSON.stringify(result));changed.context.mode='static_candidate';changed.findings=[];assert.deepEqual(compare(result,changed).no_longer_detected,[]);
  const narrowed=JSON.parse(JSON.stringify(partial));narrowed.scope=['file:fixture.php'];narrowed.status='completed';narrowed.findings=[];assert.deepEqual(compare(partial,narrowed).no_longer_detected,[]);
  validate(result);
});
test('Size and API identity bounds prevent clean unsupported scans',()=>{
  const p=input('DB-34');p.context.db_apis.pdo.version='8.1';assert.equal(evaluate(p).status,'unavailable');
  const q=input('DB-34');q.sources[0].content+=' '.repeat(262145);assert.equal(evaluate(q).status,'error');
  const r=input('DB-34');r.sources[0].content=r.sources[0].content.replace('  $sql','  $pdo=$unknown;\n  $sql');assert.equal(evaluate(r).status,'unavailable');
});
test('SQL proof includes immutable alias origins; hidden reference mutation prevents proof',()=>{
  const code='<?php\nfunction load($pdo) {\n $base="SELECT id FROM users WHERE 1=0";\n $sql=$base;\n return $pdo->query($sql);\n}';
  const r=run('DB-06',code).result;assert.equal(r.findings.length,1);assert.ok(r.findings[0].evidence.some(e=>e.value.includes('WHERE 1=0')));
  const mutated=code.replace(' $sql=$base;',' mutate($base);\n $sql=$base;');assert.equal(run('DB-06',mutated).result.status,'unavailable');
  const noTable='<?php function load($pdo) { return $pdo->query("SELECT 1 WHERE 1=0"); }';assert.equal(run('DB-06',noTable).result.findings.length,1);
});
test('PHP long expression AST depth is bounded even without parentheses',()=>{
  const code='<?php function load($pdo) { $x='+Array(200).fill('1').join('+')+'; }';assert.equal(run('DB-06',code).result.status,'error');
});
test('Committed synthetic fixture pairs remain contract valid',()=>{
  const dir=path.join(__dirname,'fixtures','g01');
  assert.ok(fs.existsSync(dir),'Committed fixtures must be present');
  for(const name of fs.readdirSync(dir).filter(n=>n.endsWith('-input.json'))) {
    const p=JSON.parse(fs.readFileSync(path.join(dir,name),'utf8'));
    const expected=JSON.parse(fs.readFileSync(path.join(dir,name.replace('-input','-result')),'utf8'));
    assert.deepEqual(evaluate(p),expected);validatePair(p,expected);
  }
});
