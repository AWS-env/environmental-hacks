'use strict';
const esbuild=require('esbuild');
const fs=require('node:fs');
const path=require('node:path');
const out=path.join(__dirname,'../.build/owner-b');
fs.mkdirSync(out,{recursive:true});
esbuild.buildSync({entryPoints:['detectors/owner-b/handlers/static.js','detectors/owner-b/handlers/log.js'],outdir:out,bundle:true,platform:'node',target:'node22',format:'cjs',minify:false,metafile:true,write:true});
console.log('Built owner-b static/log handlers in .build/owner-b');
