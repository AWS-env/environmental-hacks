'use strict';
const clone=x=>JSON.parse(JSON.stringify(x));
const digest='a'.repeat(64);
function evidence(){
 return {format:'network-evidence-v1',repository_id:'fixture:owner-b/network',commit_sha:'b'.repeat(40),route:'catalog',
 capture:{tool:'owner-b-synthetic',version:'1',origin:'synthetic'},
 acquisition:{status:'complete',start:'2026-10-10T00:00:00Z',end:'2026-10-10T00:01:00Z'},
 trace:{complete:true,sampled:false},
 spans:Array.from({length:8},(_,i)=>({id:'s'+i,parent_id:null,request_group:'r1',destination:'inventory',operation:'get-item',role:'outbound',start_ms:i*100,end_ms:i*100+90,bytes:100,setup_ms:20,dependencies:[],authorization_key:digest})),
 batch_policy:{supported:true,required_streaming:false,documentation:'https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_BatchGetItem.html'}};
}
function input(check,data=evidence()){
 return {schema_version:'1.0',kind:'input',repository_id:data.repository_id,scan_id:'fixture-'+check,commit_sha:data.commit_sha,check_id:check,detector_version:'1.0.0',
 context:{rule_version:'network-1',mode:'runtime',min_calls:5,max_small_bytes:1024},
 scope:['route:catalog'],sources:[{source_id:'capture',scope_id:'route:catalog',kind:'artifact',locator:'fixture:network-capture.json',data}]};
}
module.exports={clone,digest,evidence,input};
