'use strict';
const cdk=require('aws-cdk-lib');
const {aws_s3:s3,aws_lambda:lambda,aws_logs:logs,aws_iam:iam,aws_sqs:sqs}=cdk;
const app=new cdk.App();
const project=app.node.tryGetContext('project');
if(!/^\d{12}$/.test(project||''))throw new Error('Supply verified -c project=<project number>');
const env={account:project,region:'eu-north-1'};
const artifacts=new cdk.Stack(app,'OwnerBArtifacts',{stackName:'owner-b-artifacts',env,synthesizer:new cdk.BootstraplessSynthesizer()});
cdk.Tags.of(artifacts).add('owner','B');
const bucket=new s3.Bucket(artifacts,'Artifacts',{encryption:s3.BucketEncryption.S3_MANAGED,enforceSSL:true,blockPublicAccess:s3.BlockPublicAccess.BLOCK_ALL,versioned:true,removalPolicy:cdk.RemovalPolicy.RETAIN,lifecycleRules:[{prefix:'inputs/',expiration:cdk.Duration.days(7),noncurrentVersionExpiration:cdk.Duration.days(7)},{prefix:'results/',expiration:cdk.Duration.days(30),noncurrentVersionExpiration:cdk.Duration.days(30)}]});
new cdk.CfnOutput(artifacts,'ArtifactBucket',{value:bucket.bucketName});
const name=app.node.tryGetContext('artifactBucket'),key=app.node.tryGetContext('codeKey');
if(name&&key) {
  const codeVersion=app.node.tryGetContext('codeVersion');
  if(!codeVersion)throw new Error('Supply immutable S3 codeVersion from HeadObject');
  const stack=new cdk.Stack(app,'OwnerBG01',{stackName:'owner-b-g01',env,synthesizer:new cdk.BootstraplessSynthesizer()});
  cdk.Tags.of(stack).add('owner','B');
  const shared=s3.Bucket.fromBucketName(stack,'ExistingArtifactBucket',name);
  const hub=app.node.tryGetContext('hubArn')||'';
  const groups=JSON.parse(app.node.tryGetContext('logGroups')||'[]');
  const reserved=Number(app.node.tryGetContext('reservedConcurrency')||0);
  for(const family of ['static','log']) {
    const functionName=family==='static'?'owner-b-static-scan':'owner-b-log-analyzer';
    const group=new logs.LogGroup(stack,family+'Logs',{logGroupName:'/aws/lambda/'+functionName,retention:logs.RetentionDays.ONE_WEEK,removalPolicy:cdk.RemovalPolicy.RETAIN});
    const dlq=new sqs.Queue(stack,family+'Failures',{encryption:sqs.QueueEncryption.SQS_MANAGED,retentionPeriod:cdk.Duration.days(7)});
    const role=new iam.Role(stack,family+'ExecutionRole',{assumedBy:new iam.ServicePrincipal('lambda.amazonaws.com')});
    role.addToPolicy(new iam.PolicyStatement({actions:['logs:CreateLogStream','logs:PutLogEvents'],resources:[group.logGroupArn]}));
    const fn=new lambda.Function(stack,family+'Function',{functionName,role,runtime:lambda.Runtime.NODEJS_22_X,architecture:lambda.Architecture.ARM_64,handler:family+'.handler',code:lambda.Code.fromBucket(shared,key,codeVersion),memorySize:512,timeout:cdk.Duration.seconds(90),logGroup:group,...(reserved?{reservedConcurrentExecutions:reserved}:{}),environment:{ARTIFACT_BUCKET:name,FINDINGS_HUB_ARN:hub,QUERY_LOG_GROUPS:JSON.stringify(groups)},deadLetterQueue:dlq,retryAttempts:1});
    fn.addToRolePolicy(new iam.PolicyStatement({actions:['s3:GetObject'],resources:[shared.arnForObjects('inputs/*')]}));
    fn.addToRolePolicy(new iam.PolicyStatement({actions:['s3:PutObject'],resources:[shared.arnForObjects('results/*')]}));
    if(hub)fn.addToRolePolicy(new iam.PolicyStatement({actions:['events:PutEvents'],resources:[hub]}));
    if(family==='log'&&groups.length) {
      fn.addToRolePolicy(new iam.PolicyStatement({actions:['logs:StartQuery'],resources:groups.map(g=>`arn:aws:logs:eu-north-1:${project}:log-group:${g}:*`)}));
      // GetQueryResults/StopQuery do not support resource-level permissions; no reads or writes beyond queries.
      fn.addToRolePolicy(new iam.PolicyStatement({actions:['logs:GetQueryResults','logs:StopQuery'],resources:['*']}));
    }
    new cdk.CfnOutput(stack,family+'Arn',{value:fn.functionArn});
    new cdk.CfnOutput(stack,family+'VersionArn',{value:fn.currentVersion.functionArn});
  }
  new cdk.CfnOutput(stack,'HubStatus',{value:hub?'Configured; verify ingestion and report readback':'BLOCKED: Owner D hub missing'});
  new cdk.CfnOutput(stack,'RealLogsStatus',{value:groups.length?'Allowlist configured; real client acquisition NOT certified':'BLOCKED: real query log group missing'});
}
app.synth();
