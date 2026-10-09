"""Synthetic LLM-09 marker: per-request metadata lets Bedrock invocation logs attribute token usage."""

import boto3

bedrock = boto3.client("bedrock-runtime")


def answer(question):
    response = bedrock.converse(
        modelId="amazon.nova-micro-v1:0",
        messages=[{"role": "user", "content": [{"text": question}]}],
        requestMetadata={"feature": "search"},
    )
    return response["output"]["message"]["content"][0]["text"]
