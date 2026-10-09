'use strict';
const database=require('./g01');
const jobs={
  'JOB-04':require('../checks/job-04'),
  'JOB-03':require('../checks/job-03'),
  'JOB-05':require('../checks/job-05'),
  'JOB-01':require('../checks/job-01'),
  'JOB-06':require('../checks/job-06'),
};
const network={
  "NET-09":require('../checks/net-09'),
  "NET-08":require('../checks/net-08'),
  "NET-07":require('../checks/net-07'),
  "NET-06":require('../checks/net-06'),
  "NET-05":require('../checks/net-05'),
  "NET-04":require('../checks/net-04'),
  "NET-03":require('../checks/net-03'),
  "NET-02":require('../checks/net-02'),
  'NET-01':require('../checks/net-01'),
};
function evaluate(input){return network[input.check_id]?network[input.check_id].evaluate(input):jobs[input.check_id]?jobs[input.check_id].evaluate(input):database.evaluate(input);}
module.exports={evaluate,jobs,network};
