'use strict';
const {processJobs}=require('./jobs-pipeline');
module.exports.handler=(event,context)=>processJobs('telemetry',event,context);
