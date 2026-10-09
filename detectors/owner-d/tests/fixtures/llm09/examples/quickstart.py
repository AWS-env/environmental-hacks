"""Synthetic LLM-09 exempt path: a documentation example."""

import boto3

bedrock = boto3.client("bedrock-runtime")
response = bedrock.converse(modelId="amazon.nova-micro-v1:0", messages=[{"role": "user", "content": [{"text": "hi"}]}])
print(response["output"]["message"]["content"][0]["text"])
