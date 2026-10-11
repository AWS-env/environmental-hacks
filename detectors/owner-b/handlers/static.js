'use strict';
const {processInput}=require('./pipeline');
const {processJobs}=require('./jobs-pipeline');
const {orm}=require('../core/dispatch');
module.exports.handler=(event,context)=>processInputOrJobs(event,context);
function processInputOrJobs(event,context){const id=event?.check_id||event?.input?.check_id;return id?.startsWith('JOB-')||orm[id]||event?.family==='jobs'||event?.family==='orm'?processJobs('static',event,context):processInput('static',event,context);}
