'use strict';
const cdk=require('aws-cdk-lib');
const {aws_s3:s3,aws_lambda:lambda,aws_logs:logs,aws_sqs:sqs,aws_iam:iam}=cdk;
function createNetwork(app){
 const project=app.node.tryGetContext('project'),region=app.node.tryGetContext('region'),bucketName=app.node.tryGetContext('artifactBucket'),codeKey=app.node.tryGetContext('codeKey'),codeVersion=app.node.tryGetContext('codeVersion'),hub=app.node.tryGetContext('hubArn');
 if(!/^\d{12}$/.test(project||'')||region!=='ap-south-1'||!bucketName||!codeKey||!codeVersion||!hub?.startsWith(`arn:aws:events:${region}:${project}:event-bus/`))throw new Error('Require verified same-Region project, bucket, immutable code and hub');
 const stack=new cdk.Stack(app,'OwnerBNetwork',{stackName:'owner-b-network',env:{account:project,region},synthesizer:new cdk.BootstraplessSynthesizer()});
 cdk.Tags.of(stack).add('owner','B');
 const bucket=s3.Bucket.fromBucketName(stack,'Artifacts',bucketName),functions={};
 for(const family of ['trace','profile','upload']){
  const name={trace:'owner-b-trace-analyzer',profile:'owner-b-profile-parser',upload:'owner-b-network-upload'}[family];
  const log=new logs.LogGroup(stack,family+'Logs',{logGroupName:'/aws/lambda/'+name,retention:logs.RetentionDays.ONE_WEEK,removalPolicy:cdk.RemovalPolicy.RETAIN});
  const dlq=new sqs.Queue(stack,family+'Failures',{encryption:sqs.QueueEncryption.SQS_MANAGED,retentionPeriod:cdk.Duration.days(7)});
  const role=new iam.Role(stack,family+'Role',{assumedBy:new iam.ServicePrincipal('lambda.amazonaws.com')});
  role.addToPolicy(new iam.PolicyStatement({actions:['logs:CreateLogStream','logs:PutLogEvents'],resources:[log.logGroupArn]}));
  const fn=new lambda.Function(stack,family+'Function',{functionName:name,role,logGroup:log,runtime:lambda.Runtime.NODEJS_22_X,architecture:lambda.Architecture.ARM_64,code:lambda.Code.fromBucket(bucket,codeKey,codeVersion),handler:family==='upload'?'network-upload.handler':'network.handler',memorySize:512,timeout:cdk.Duration.seconds(90),deadLetterQueue:dlq,retryAttempts:1,environment:{ARTIFACT_BUCKET:bucketName,FINDINGS_HUB_ARN:hub,NETWORK_SOURCES:app.node.tryGetContext('networkSources')||'{}',AUTHORIZED_REPOSITORIES:app.node.tryGetContext('repositories')||'[]'}});
  if(family==='upload')fn.addToRolePolicy(new iam.PolicyStatement({actions:['s3:PutObject'],resources:[bucket.arnForObjects('inputs/network/*')]}));
  else{
   fn.addToRolePolicy(new iam.PolicyStatement({actions:['s3:GetObject','s3:GetObjectVersion'],resources:[bucket.arnForObjects('inputs/network/*')]}));
   fn.addToRolePolicy(new iam.PolicyStatement({actions:['s3:PutObject'],resources:[bucket.arnForObjects('results/network/*')]}));
   fn.addToRolePolicy(new iam.PolicyStatement({actions:['events:PutEvents'],resources:[hub]}));
  }
  if(family==='trace'){
   fn.addToRolePolicy(new iam.PolicyStatement({actions:['xray:GetTraceSummaries','xray:BatchGetTraces','cloudwatch:GetMetricData'],resources:['*'],conditions:{StringEquals:{'aws:RequestedRegion':region}}}));
   const groups=[...new Set(Object.values(JSON.parse(app.node.tryGetContext('networkSources')||'{}')).filter(s=>s.kind==='logs').map(s=>s.group))];
   if(groups.length){fn.addToRolePolicy(new iam.PolicyStatement({actions:['logs:StartQuery'],resources:groups.map(g=>`arn:aws:logs:${region}:${project}:log-group:${g}:*`)}));fn.addToRolePolicy(new iam.PolicyStatement({actions:['logs:GetQueryResults','logs:StopQuery'],resources:['*'],conditions:{StringEquals:{'aws:RequestedRegion':region}}}));}
  }
  if(family==='profile')fn.currentVersion.addPermission('ArtifactEvent',{principal:new iam.ServicePrincipal('s3.amazonaws.com'),sourceArn:bucket.bucketArn,sourceAccount:project});
  functions[family]=fn;
  new cdk.CfnOutput(stack,family+'Version',{value:fn.currentVersion.functionArn});
 }
 new cdk.CfnOutput(stack,'ArtifactConsumerArn',{value:functions.profile.currentVersion.functionArn});
 return {stack,functions};
}
if(require.main===module){const app=new cdk.App();createNetwork(app);app.synth();}
module.exports={createNetwork};
