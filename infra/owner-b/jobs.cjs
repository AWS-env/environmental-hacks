'use strict';
const cdk=require('aws-cdk-lib');
const {aws_s3:s3,aws_lambda:lambda,aws_logs:logs,aws_iam:iam,aws_sqs:sqs}=cdk;
function createJobs(app){
  const project=app.node.tryGetContext('project'),region=app.node.tryGetContext('region');
  if(!/^\d{12}$/.test(project||'')||region!=='ap-south-1')throw new Error('Supply verified project and explicit selected Region ap-south-1');
  const env={account:project,region};
  const artifacts=new cdk.Stack(app,'OwnerBJobsArtifacts',{stackName:'owner-b-jobs-artifacts',env,synthesizer:new cdk.BootstraplessSynthesizer(),terminationProtection:true});
  cdk.Tags.of(artifacts).add('owner','B');
  const bucket=new s3.Bucket(artifacts,'Artifacts',{encryption:s3.BucketEncryption.S3_MANAGED,enforceSSL:true,blockPublicAccess:s3.BlockPublicAccess.BLOCK_ALL,versioned:true,removalPolicy:cdk.RemovalPolicy.RETAIN,lifecycleRules:[{prefix:'inputs/',expiration:cdk.Duration.days(7),noncurrentVersionExpiration:cdk.Duration.days(7)},{prefix:'results/',expiration:cdk.Duration.days(30),noncurrentVersionExpiration:cdk.Duration.days(30)}]});
  new cdk.CfnOutput(artifacts,'ArtifactBucket',{value:bucket.bucketName});
  const name=app.node.tryGetContext('artifactBucket'),key=app.node.tryGetContext('codeKey');
  if(!name||!key)return {artifacts};
  const version=app.node.tryGetContext('codeVersion');if(!version)throw new Error('Immutable S3 codeVersion required');
  const stack=new cdk.Stack(app,'OwnerBJobs',{stackName:'owner-b-jobs',env,synthesizer:new cdk.BootstraplessSynthesizer()});
  cdk.Tags.of(stack).add('owner','B');
  const shared=s3.Bucket.fromBucketName(stack,'ExistingArtifacts',name),hub=app.node.tryGetContext('hubArn')||'';
  if(hub&&!hub.startsWith(`arn:aws:events:${region}:${project}:event-bus/`))throw new Error('Hub must be in the verified project and Region');
  const jobSources=JSON.parse(app.node.tryGetContext('jobLogSources')||'{}'),pools=JSON.parse(app.node.tryGetContext('workerPools')||'{}'),groups=JSON.parse(app.node.tryGetContext('scheduleGroups')||'{}');
  const smokeGroup=app.node.tryGetContext('smokeLogGroup')||'';
  for(const family of ['static','log','telemetry','heuristic']){
    const functionName={static:'owner-b-static-scan',log:'owner-b-log-analyzer',telemetry:'owner-b-telemetry-analyzer',heuristic:'owner-b-heuristic-scan'}[family];
    const group=new logs.LogGroup(stack,family+'Logs',{logGroupName:'/aws/lambda/'+functionName,retention:logs.RetentionDays.ONE_WEEK,removalPolicy:cdk.RemovalPolicy.RETAIN});
    const dlq=new sqs.Queue(stack,family+'Failures',{encryption:sqs.QueueEncryption.SQS_MANAGED,retentionPeriod:cdk.Duration.days(7)});
    const role=new iam.Role(stack,family+'Role',{assumedBy:new iam.ServicePrincipal('lambda.amazonaws.com')});
    role.addToPolicy(new iam.PolicyStatement({actions:['logs:CreateLogStream','logs:PutLogEvents'],resources:[group.logGroupArn]}));
    const fn=new lambda.Function(stack,family+'Function',{functionName,role,runtime:lambda.Runtime.NODEJS_22_X,architecture:lambda.Architecture.ARM_64,handler:family+'.handler',code:lambda.Code.fromBucket(shared,key,version),memorySize:512,timeout:cdk.Duration.seconds(90),logGroup:group,deadLetterQueue:dlq,retryAttempts:1,environment:{ARTIFACT_BUCKET:name,FINDINGS_HUB_ARN:hub,JOB_LOG_SOURCES:JSON.stringify(jobSources),WORKER_POOLS:JSON.stringify(pools),SCHEDULE_GROUPS:JSON.stringify(groups),QUERY_LOG_GROUPS:JSON.stringify(smokeGroup?[smokeGroup]:[])}});
    fn.addToRolePolicy(new iam.PolicyStatement({actions:['s3:GetObject'],resources:[shared.arnForObjects('inputs/*')]}));
    fn.addToRolePolicy(new iam.PolicyStatement({actions:['s3:PutObject'],resources:[shared.arnForObjects('results/*')]}));
    if(hub)fn.addToRolePolicy(new iam.PolicyStatement({actions:['events:PutEvents'],resources:[hub]}));
    if(family==='telemetry')fn.addToRolePolicy(new iam.PolicyStatement({actions:['cloudwatch:GetMetricData'],resources:['*']}));
    if(family==='static'&&Object.keys(groups).length){
      fn.addToRolePolicy(new iam.PolicyStatement({actions:['scheduler:ListSchedules'],resources:['*']}));
      fn.addToRolePolicy(new iam.PolicyStatement({actions:['scheduler:GetSchedule'],resources:Object.keys(groups).map(g=>`arn:aws:scheduler:${region}:${project}:schedule/${g}/*`)}));
    }
    if(family==='log'){
      const allowed=[...new Set(Object.values(jobSources).map(v=>v.group).concat(smokeGroup?[smokeGroup]:[]))];
      if(allowed.length){fn.addToRolePolicy(new iam.PolicyStatement({actions:['logs:StartQuery'],resources:allowed.map(g=>`arn:aws:logs:${region}:${project}:log-group:${g}:*`)}));fn.addToRolePolicy(new iam.PolicyStatement({actions:['logs:GetQueryResults','logs:StopQuery'],resources:['*']}));}
    }
    new cdk.CfnOutput(stack,family+'Version',{value:fn.currentVersion.functionArn});
  }
  return {artifacts,stack};
}
if(require.main===module){const app=new cdk.App();createJobs(app);app.synth();}
module.exports={createJobs};
