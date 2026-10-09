"""Synthetic LLM-09 positive: Bedrock responses are read for their text only."""

import json

import boto3

bedrock = boto3.client("bedrock-runtime", region_name="ap-south-1")
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"


def lambda_handler(event, context):
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": event["question"]}]}],
        inferenceConfig={"maxTokens": 512},
    )
    answer = response["output"]["message"]["content"][0]["text"]
    return {"statusCode": 200, "body": answer}


def classify(text):
    body = json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 16, "messages": [text]})
    raw = bedrock.invoke_model(modelId=MODEL_ID, body=body)
    payload = json.loads(raw["body"].read())
    return payload["content"][0]["text"].strip()


def stream_reply(prompt):
    response = bedrock.converse_stream(modelId=MODEL_ID, messages=[{"role": "user", "content": [{"text": prompt}]}])
    parts = []
    for event in response["stream"]:
        if "contentBlockDelta" in event:
            parts.append(event["contentBlockDelta"]["delta"]["text"])
    return "".join(parts)
