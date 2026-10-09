'use strict';
const database=require('./g01');
const jobs={
  'JOB-04':require('../checks/job-04'),
  'JOB-03':require('../checks/job-03'),
};
function evaluate(input){return jobs[input.check_id]?jobs[input.check_id].evaluate(input):database.evaluate(input);}
module.exports={evaluate,jobs};
