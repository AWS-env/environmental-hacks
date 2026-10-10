"""Synthetic fixture for LLM-18: an agent calling Bedrock and OpenSearch Serverless in other Regions."""
import os

import boto3
from langchain_aws import ChatBedrockConverse
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

MODEL_REGION = "us-west-2"
STORE_HOST = "abc123xyz.eu-west-1.aoss.amazonaws.com"

bedrock = boto3.client("bedrock-runtime", region_name=MODEL_REGION)
uploads = boto3.client("s3", region_name="eu-west-1")
auth = AWSV4SignerAuth(boto3.Session().get_credentials(), "eu-west-1", "aoss")
search = OpenSearch(
    hosts=[{"host": STORE_HOST, "port": 443}],
    http_auth=auth,
    use_ssl=True,
    connection_class=RequestsHttpConnection,
)


def summarizer():
    return ChatBedrockConverse(model="anthropic.claude-3-5-haiku-20241022-v1:0", region_name=os.environ["AWS_REGION"])


def handler(event, context):
    reply = bedrock.converse(modelId="anthropic.claude-3-5-sonnet-20240620-v1:0", messages=event["messages"])
    return reply["output"]
