'use strict';
const {processInput}=require('./pipeline');
module.exports.handler=(event,context)=>processInput('static',event,context);
