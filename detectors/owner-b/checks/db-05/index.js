'use strict';
const {evaluate}=require('../../core/g01');
module.exports={evaluate(input){if(input.check_id!=='DB-05')throw new Error('Expected DB-05');return evaluate(input);}};
