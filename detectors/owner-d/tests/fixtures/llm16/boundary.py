# Synthetic LLM-16 fixture: output-cap boundary, free tool choice and repeated anchors. Never executed.
import anthropic
import boto3
from flask import Flask

claude = anthropic.Anthropic()
bedrock = boto3.client("bedrock-runtime")
app = Flask(__name__)
MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


@app.post("/at-limit")
def at_limit(messages):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, messages=messages)


@app.post("/over-limit")
def over_limit(messages):
    first = claude.messages.create(model="claude-sonnet-4-5", max_tokens=257, messages=messages)
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=257, messages=[first], tools=TOOLS, tool_choice={"type": "auto"})


@app.post("/bedrock")
def bedrock_free(messages):
    small = bedrock.converse(modelId=MODEL, messages=messages, inferenceConfig={"maxTokens": 256})
    config = {"tools": TOOLS, "toolChoice": {"auto": {}}}
    return small, bedrock.converse(modelId=MODEL, messages=messages, toolConfig=config)


TOOLS = [{"name": "route", "description": "Route the ticket.", "input_schema": {"type": "object", "properties": {}}}]
