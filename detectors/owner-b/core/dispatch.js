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
  "NET-12":require('../checks/net-12'),
  "NET-11":require('../checks/net-11'),
  "NET-10":require('../checks/net-10'),
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
const orm={
  'DB-09':require('../checks/db-09'),
  'DB-13':require('../checks/db-13'),
  'DB-14':require('../checks/db-14'),
  'DB-23':require('../checks/db-23'),
  'DB-41':require('../checks/db-41'),
  'DB-29':require('../checks/db-29'),
  'DB-39':require('../checks/db-39'),
  'DB-43':require('../checks/db-43'),
  'DB-45':require('../checks/db-45'),
  'DB-46':require('../checks/db-46'),
};
// ORM checks parse with WASM tree-sitter and return a Promise; the other checks are synchronous.
function evaluate(input){return orm[input.check_id]?orm[input.check_id].evaluate(input):network[input.check_id]?network[input.check_id].evaluate(input):jobs[input.check_id]?jobs[input.check_id].evaluate(input):database.evaluate(input);}
module.exports={evaluate,jobs,network,orm};
