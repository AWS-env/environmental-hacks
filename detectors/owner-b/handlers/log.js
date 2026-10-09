'use strict';
const {processInput}=require('./pipeline');
module.exports.handler=(event,context)=>processInput('log',event,context);
