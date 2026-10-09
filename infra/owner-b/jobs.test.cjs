'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),cdk=require('aws-cdk-lib'),{Template}=require('aws-cdk-lib/assertions');
const {createJobs}=require('./jobs.cjs');
test('Job deployment is region-bound, private, retained and least privilege',()=>{
  const app=new cdk.App({context:{project:'111111111111',region:'ap-south-1',artifactBucket:'synthetic-artifacts',codeKey:'code/hash.zip',codeVersion:'synthetic-version',hubArn:'arn:aws:events:ap-south-1:111111111111:event-bus/synthetic-hub'}}),{artifacts,stack}=createJobs(app);
  const a=Template.fromStack(artifacts),t=Template.fromStack(stack);a.resourceCountIs('AWS::S3::Bucket',1);t.resourceCountIs('AWS::Lambda::Function',4);t.resourceCountIs('AWS::SQS::Queue',4);t.resourceCountIs('AWS::Lambda::Url',0);t.resourceCountIs('AWS::DynamoDB::Table',0);
  for(const resource of Object.values(t.toJSON().Resources).filter(x=>x.Type==='AWS::Lambda::Function')){assert.equal(resource.Properties.Timeout,90);assert.equal(resource.Properties.MemorySize,512);assert.equal(resource.Properties.Environment.Variables.AWS_REGION,undefined);}
  const statements=Object.values(t.toJSON().Resources).filter(x=>x.Type==='AWS::IAM::Policy').flatMap(x=>x.Properties.PolicyDocument.Statement);
  assert.equal(statements.some(x=>[].concat(x.Action).some(a=>/PutMetricData|CreateSchedule|UpdateSchedule|dynamodb:/.test(a))),false);
  assert.equal(Object.values(a.toJSON().Resources).find(x=>x.Type==='AWS::S3::Bucket').DeletionPolicy,'Retain');
});
test('Unverified project or different Region fails before synthesis',()=>{assert.throws(()=>createJobs(new cdk.App({context:{project:'111111111111',region:'eu-north-1'}})));});
