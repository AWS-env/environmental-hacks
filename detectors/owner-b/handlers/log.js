'use strict';
const {processInput}=require('./pipeline');
const {processJobs}=require('./jobs-pipeline');
module.exports.handler=(event,context)=>event?.check_id==='JOB-05'||event?.input?.check_id==='JOB-05'||event?.family==='jobs'?processJobs('log',event,context):processInput('log',event,context);
