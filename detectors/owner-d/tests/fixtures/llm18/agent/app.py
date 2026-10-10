"""Support agent that answers tickets with Bedrock (synthetic LLM-18 fixture).

Docs: https://bedrock-runtime.us-west-2.amazonaws.com is only mentioned here.
"""

import os

import boto3
from anthropic import AnthropicBedrock
from botocore.config import Config

MODEL_REGION = "us-east-1"

runtime = boto3.client("bedrock-runtime", region_name=MODEL_REGION)
local = boto3.client("bedrock-runtime", region_name="ap-south-1")
from_env = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
control = boto3.client("bedrock", region_name="us-east-1")
claude = AnthropicBedrock(aws_region="us-west-2")
retry = boto3.client(
    "bedrock-runtime",
    config=Config(region_name="eu-west-1", retries={"max_attempts": 3}),
)


def knowledge_base():
    session = boto3.Session(region_name="us-east-1")
    return session.client("bedrock-agent-runtime")


def answer(question):
    # https://bedrock-runtime.us-west-2.amazonaws.com in a comment is not a dependency
    response = runtime.converse(
        modelId="anthropic.claude-3-haiku-20240307-v1:0",
        messages=[{"role": "user", "content": [{"text": question}]}],
    )
    return response["output"]["message"]["content"][0]["text"]


deliberate = boto3.client("bedrock-runtime", region_name="us-east-2")  # noqa: LLM-18
