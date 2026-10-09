'use strict';
const crypto=require('node:crypto');
const {S3Client,PutObjectCommand}=require('@aws-sdk/client-s3');
const {getSignedUrl}=require('@aws-sdk/s3-request-presigner');
const n=require('../core/network');
const client=new S3Client({region:process.env.AWS_REGION||'ap-south-1',maxAttempts:2});
/** Invoke permission is the authenticated connector boundary; server repo allowlist is mandatory.
 * @param {any} event @param {any} _context @param {any} [deps] */
async function handler(event,_context,deps={}){
 const allowed=deps.repositories||JSON.parse(process.env.AUTHORIZED_REPOSITORIES||'[]');
 n.requireValue(allowed.includes(event.repository_id),'Repository not authorized');n.digest(event.sha256);
 n.requireValue(Number.isInteger(event.bytes)&&event.bytes>0&&event.bytes<=1048576,'Upload size bound');
 const bucket=deps.bucket||process.env.ARTIFACT_BUCKET;n.text(bucket,'bucket');
 const repo=crypto.createHash('sha256').update(event.repository_id).digest('hex').slice(0,32),key='inputs/network/'+repo+'/'+event.sha256+'.json';
 const checksum=Buffer.from(event.sha256,'hex').toString('base64');
 const command=new PutObjectCommand({Bucket:bucket,Key:key,ContentType:'application/json',ContentLength:event.bytes,ChecksumSHA256:checksum,IfNoneMatch:'*'});
 const url=await (deps.sign||getSignedUrl)(deps.s3||client,command,{expiresIn:300,unhoistableHeaders:new Set(['x-amz-checksum-sha256']),signableHeaders:new Set(['content-type','content-length','if-none-match'])});
 return {url,key,expires_seconds:300,headers:{'content-type':'application/json','content-length':String(event.bytes),'x-amz-checksum-sha256':checksum,'if-none-match':'*'}};
}
module.exports={handler};
