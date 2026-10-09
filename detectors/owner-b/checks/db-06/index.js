'use strict';
const {evaluate}=require('../../core/g01');
module.exports={evaluate(input){if(input.check_id!=='DB-06')throw new Error('Expected DB-06');return evaluate(input);}};
