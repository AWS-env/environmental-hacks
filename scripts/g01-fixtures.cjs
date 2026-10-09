'use strict';
// Generate only explicitly synthetic test cases, never AWS acceptance receipts.
const fs=require('node:fs');
const path=require('node:path');
const {input,withEvents}=require('../detectors/owner-b/test/g01-fixtures');
const {evaluate}=require('../detectors/owner-b/core/g01');
const dir=path.join(__dirname,'../detectors/owner-b/test/fixtures/g01');
fs.mkdirSync(dir,{recursive:true});
for(const check of ['DB-34','DB-06','DB-05','DB-16']) {
  const base=input(check);
  const negatives={
    'DB-34':'<?php\nfunction load($pdo,$cache) {\n $cached=$cache->get("users");\n if ($cached !== null) { return $cached; }\n $sql="SELECT id FROM users";\n return $pdo->query($sql);\n}',
    'DB-06':input(check).sources[0].content.replace('1=0','id=7'),
    'DB-05':input(check).sources[0].content.replace('  $data = $pdo->query("SELECT id FROM articles");','  consume($data);\n  $data = $pdo->query("SELECT id FROM articles");'),
    'DB-16':'<?php\nfunction load($pdo) {\n return $pdo->query("SELECT id FROM users");\n}'
  };
  const exception=input(check,check==='DB-34'?base.sources[0].content.replace('  $cached','  audit($sql);\n  $cached'):base.sources[0].content.replace('SELECT id FROM users','SELECT id FROM users FOR UPDATE'));
  const missing=input(check);missing.sources=[];
  const malformed=input(check,'<?php function broken( {');
  const boundary=input(check,check==='DB-34'?base.sources[0].content.replace('!== null','=== null'):check==='DB-06'?base.sources[0].content.replace('1=0','NULL=1'):base.sources[0].content);
  const cases=[base,input(check,negatives[check]),exception,missing,malformed,boundary];
  for(let i=0;i<cases.length;i++) {
    const p=cases[i];p.scan_id='synthetic:'+check+'-0'+(i+1);
    if(['DB-05','DB-16'].includes(check)&&p.sources.length) {
      withEvents(p);
      if(i===5)p.sources[1].data.events[0].commit_sha='b'.repeat(40);
    }
    const stem=check.toLowerCase()+'-0'+(i+1);
    fs.writeFileSync(path.join(dir,stem+'-input.json'),JSON.stringify(p,null,2)+'\n');
    fs.writeFileSync(path.join(dir,stem+'-result.json'),JSON.stringify(evaluate(p),null,2)+'\n');
  }
}
