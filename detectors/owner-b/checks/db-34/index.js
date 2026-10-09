'use strict';
const {evaluate}=require('../../core/g01');
module.exports={evaluate(input){if(input.check_id!=='DB-34')throw new Error('Expected DB-34');return evaluate(input);}};
