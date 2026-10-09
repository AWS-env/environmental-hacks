'use strict';
const {processJobs}=require('./jobs-pipeline');
module.exports.handler=(event,context)=>processJobs('heuristic',event,context);
