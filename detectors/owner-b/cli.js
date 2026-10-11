'use strict';
const fs=require('node:fs');
const {evaluate}=require('./core/dispatch');
async function main() {
  const file=process.argv[2];
  if(!file)throw new Error('Usage: node detectors/owner-b/cli.js INPUT.json');
  if(fs.statSync(file).size>1024*1024)throw new Error('Input exceeds 1 MiB');
  // ORM checks return a Promise; awaiting a synchronous result is harmless.
  process.stdout.write(JSON.stringify(await evaluate(JSON.parse(fs.readFileSync(file,'utf8'))),null,2)+'\n');
}
if(require.main===module) main().catch(e=>{process.stderr.write(e.message+'\n');process.exitCode=1;});
