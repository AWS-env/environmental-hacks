'use strict';
const {processInput}=require('./pipeline');
const {processJobs}=require('./jobs-pipeline');
module.exports.handler=(event,context)=>processInputOrJobs(event,context);
function processInputOrJobs(event,context){return event?.check_id?.startsWith('JOB-')||event?.input?.check_id?.startsWith('JOB-')||event?.family==='jobs'?processJobs('static',event,context):processInput('static',event,context);}
