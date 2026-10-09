'use strict';
const {evaluate}=require('../../core/g01');
module.exports={evaluate(input){if(input.check_id!=='DB-16')throw new Error('Expected DB-16');return evaluate(input);}};
