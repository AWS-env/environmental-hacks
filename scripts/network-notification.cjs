'use strict';
// Transform the deployed artifact stack's original JSON template, preserving its resources.
const fs=require('node:fs');
function prepare(template,arn,project){
 if(!/^\d{12}$/.test(project)||!new RegExp('^arn:aws:lambda:ap-south-1:'+project+':function:owner-b-profile-parser:[1-9][0-9]*$').test(arn))throw new Error('Require verified project and published same-Region parser version');
 const output=JSON.parse(JSON.stringify(template));
 const buckets=Object.values(output.Resources).filter(r=>r.Type==='AWS::S3::Bucket');
 if(buckets.length!==1||buckets[0].DeletionPolicy!=='Retain')throw new Error('Require the retained artifact stack with exactly one bucket');
 const properties=buckets[0].Properties,configuration=properties.NotificationConfiguration||{};
 const entries=configuration.LambdaConfigurations||[];
 const owned=e=>e.Filter?.S3Key?.Rules?.some(r=>r.Name==='prefix'&&r.Value==='inputs/network/');
 properties.NotificationConfiguration={...configuration,LambdaConfigurations:[...entries.filter(e=>!owned(e)),{Event:'s3:ObjectCreated:*',Function:arn,Filter:{S3Key:{Rules:[{Name:'prefix',Value:'inputs/network/'},{Name:'suffix',Value:'.json'}]}}}]};
 return output;
}
if(require.main===module){const [input,arn,project,output]=process.argv.slice(2);if(!output)throw new Error('Usage: network-notification.cjs original-template.json published-parser-arn project-id output.json');fs.writeFileSync(output,JSON.stringify(prepare(JSON.parse(fs.readFileSync(input,'utf8')),arn,project),null,2)+'\n');}
module.exports={prepare};
