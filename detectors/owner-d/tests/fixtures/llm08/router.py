# Synthetic LLM-08 fixture: a Bedrock prompt router is used in this file. Never executed.
import boto3

bedrock = boto3.client("bedrock-runtime")
ROUTER = "arn:aws:bedrock:ap-south-1:111122223333:default-prompt-router/anthropic.claude:1"


def routed(ticket):
    return bedrock.converse(modelId=ROUTER, messages=ticket, inferenceConfig={"maxTokens": 64})


def pinned(ticket):
    return bedrock.converse(modelId="apac.anthropic.claude-opus-4-1-20250805-v1:0", messages=ticket, inferenceConfig={"maxTokens": 64})
