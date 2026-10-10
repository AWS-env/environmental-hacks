# Synthetic LLM-16 fixture: replies that must be complete, unknown arguments and noqa. Never executed.
import asyncio
import json

import anthropic
import boto3
import instructor
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

from .server import api

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
app = FastAPI()
MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


class Ticket(BaseModel):
    title: str


@app.post("/structured")
def structured(request: dict):
    parsed = client.chat.completions.parse(model="gpt-5.5", messages=request["messages"], response_format=Ticket)
    as_json = client.chat.completions.create(model="gpt-5.5", messages=request["messages"], response_format={"type": "json_object"})
    shaped = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=4096, messages=request["messages"], output_config={"format": {"type": "json_schema"}}
    )
    texted = client.responses.create(model="gpt-5.5", input=request["text"], text={"format": {"type": "json_schema"}})
    configured = bedrock.converse(modelId=MODEL, messages=request["messages"], outputConfig={"textFormat": {"type": "json"}})
    return parsed, as_json, shaped, texted, configured


@app.post("/forced")
def forced(request: dict):
    tool = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=4096, messages=request["messages"], tools=TOOLS, tool_choice={"type": "tool", "name": "route"}
    )
    required = client.chat.completions.create(model="gpt-5.5", messages=request["messages"], tools=TOOLS, tool_choice="required")
    config = {"tools": TOOLS, "toolChoice": {"tool": {"name": "route"}}}
    bedrock_tool = bedrock.converse(modelId=MODEL, messages=request["messages"], toolConfig=config)
    return tool, required, bedrock_tool


@app.post("/unknown")
def unknown(request: dict, stream: bool, limit: int, body: str, **options):
    a = claude.messages.create(model="claude-sonnet-4-5", max_tokens=4096, messages=request["messages"], stream=stream)
    b = claude.messages.create(model="claude-sonnet-4-5", max_tokens=limit, messages=request["messages"])
    c = client.chat.completions.create(model="gpt-5.5", messages=request["messages"], **options)
    d = client.chat.completions.create(model="gpt-5.5", messages=request["messages"], extra_body=options)
    e = bedrock.invoke_model(modelId=MODEL, body=body)
    f = bedrock.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=json.dumps({"inputText": request["text"]}))
    g = bedrock.converse(modelId=PROMPT_ARN, promptVariables={"topic": {"text": request["text"]}})
    return a, b, c, d, e, f, g


def second_hop(messages):
    return client.chat.completions.create(model="gpt-5.5", messages=messages)


def first_hop(messages):
    return second_hop(messages)


def threaded(messages):
    return client.chat.completions.create(model="gpt-5.5", messages=messages)


@app.post("/indirect")
async def indirect(request: dict):
    await asyncio.to_thread(threaded, request["messages"])
    return first_hop(request["messages"])


@app.post("/parsed")
def parsed_reply(request: dict):
    patched = instructor.from_openai(client)
    ticket = patched.chat.completions.create(model="gpt-5.5", messages=request["messages"], response_model=Ticket)
    reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=4096, messages=request["messages"])
    return ticket, json.loads(reply.content[0].text)


@api.post("/elsewhere")
def elsewhere(request: dict):
    return client.chat.completions.create(model="gpt-5.5", messages=request["messages"])


@app.post("/suppressed")
def suppressed(request: dict):
    return client.chat.completions.create(model="gpt-5.5", messages=request["messages"])  # noqa: LLM-16


@app.post("/not-suppressed")
def not_suppressed(request: dict):
    return client.chat.completions.create(model="gpt-5.5", messages=request["messages"])  # noqa: E501


PROMPT_ARN = "arn:aws:bedrock:us-east-1:123456789012:prompt/PROMPT12345"
TOOLS = [{"name": "route", "description": "Route the ticket.", "input_schema": {"type": "object", "properties": {}}}]
