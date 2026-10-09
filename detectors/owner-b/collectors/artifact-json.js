'use strict';
const fs=require('node:fs'),n=require('../core/network');
/** @param {string} file */
function readJson(file){const stat=fs.statSync(file);n.requireValue(stat.isFile()&&stat.size<=1048576,'Input file bound');return JSON.parse(fs.readFileSync(file,'utf8'));}
module.exports={readJson};
