'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),cdk=require('aws-cdk-lib'),{Template}=require('aws-cdk-lib/assertions');
const {createNetwork}=require('./network.cjs');
test('Network stack scopes artifact access, omits public endpoints and protects existing workloads',()=>{
 const app=new cdk.App({context:{project:'123456789012',region:'ap-south-1',artifactBucket:'fixture-private',codeKey:'code/hash.zip',codeVersion:'version',hubArn:'arn:aws:events:ap-south-1:123456789012:event-bus/findings-hub'}});
 const {stack}=createNetwork(app),t=Template.fromStack(stack);t.resourceCountIs('AWS::Lambda::Function',3);t.resourceCountIs('AWS::Lambda::Url',0);t.resourceCountIs('AWS::S3::Bucket',0);
 const roles=t.findResources('AWS::IAM::Policy');for(const p of Object.values(roles)){const statements=p.Properties.PolicyDocument.Statement;assert(!statements.some(s=>JSON.stringify(s.Action).includes('Delete')));assert(!statements.some(s=>JSON.stringify(s.Action).includes('s3:*')));}
 t.hasResourceProperties('AWS::Lambda::Permission',{Principal:'s3.amazonaws.com',SourceAccount:'123456789012'});
});
test('Network stack rejects cross-Region hub and missing immutable version',()=>{
 const context={project:'123456789012',region:'ap-south-1',artifactBucket:'fixture-private',codeKey:'code/hash.zip',hubArn:'arn:aws:events:eu-north-1:123456789012:event-bus/findings-hub'};
 assert.throws(()=>createNetwork(new cdk.App({context})));
});

test('Notification update preserves bucket safeguards and unrelated triggers, replacing only its own version',()=>{
 const {prepare}=require('../../scripts/network-notification.cjs');
 const original={Resources:{Bucket:{Type:'AWS::S3::Bucket',DeletionPolicy:'Retain',Properties:{VersioningConfiguration:{Status:'Enabled'},NotificationConfiguration:{QueueConfigurations:[{Event:'s3:ObjectCreated:*',Queue:'fixture'}]}}}}};
 const first=prepare(original,'arn:aws:lambda:ap-south-1:123456789012:function:owner-b-profile-parser:1','123456789012');
 const next=prepare(first,'arn:aws:lambda:ap-south-1:123456789012:function:owner-b-profile-parser:2','123456789012');
 assert.equal(next.Resources.Bucket.Properties.NotificationConfiguration.LambdaConfigurations.length,1);
 assert.deepEqual(next.Resources.Bucket.Properties.NotificationConfiguration.QueueConfigurations,[{Event:'s3:ObjectCreated:*',Queue:'fixture'}]);
 assert.deepEqual(next.Resources.Bucket.Properties.VersioningConfiguration,{Status:'Enabled'});
 assert(!original.Resources.Bucket.Properties.NotificationConfiguration.LambdaConfigurations);
 assert.throws(()=>prepare(original,'arn:aws:lambda:eu-north-1:123456789012:function:owner-b-profile-parser:1','123456789012'));
});
