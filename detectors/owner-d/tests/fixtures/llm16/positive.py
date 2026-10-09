# Synthetic LLM-16 fixture: request handlers that buffer whole LLM replies. Never executed.
import json

import anthropic
import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from fastapi import APIRouter, FastAPI
from flask import Blueprint
from litestar import post
from openai import OpenAI

claude = anthropic.AsyncAnthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
app = FastAPI()
router = APIRouter()
bp = Blueprint("support", __name__)
agent = BedrockAgentCoreApp()
MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


@app.post("/chat")
async def chat(request: dict):
    reply = await claude.messages.create(model="claude-sonnet-4-5", max_tokens=4096, messages=request["messages"])
    return {"text": reply.content[0].text}


@router.post("/answer")
def answer(request: dict):
    return client.chat.completions.create(model="gpt-5.5", messages=request["messages"])


@app.get("/respond")
def respond(question: str):
    return client.responses.create(model="gpt-5.5", input=question, max_output_tokens=2000, stream=False)


@bp.route("/summary", methods=["POST"])
def summary():
    response = bedrock.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Summarise the ticket."}]}],
        inferenceConfig={"maxTokens": 2048},
    )
    return response["output"]["message"]


@agent.entrypoint
def invoke(payload):
    body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 8192, "messages": payload["messages"]}
    return json.loads(bedrock.invoke_model(modelId=MODEL, body=json.dumps(body))["body"].read())


async def draft_reply(messages):
    return await claude.messages.create(model="claude-sonnet-4-5", max_tokens=2048, messages=messages)


@app.post("/reply")
async def reply(request: dict):
    return await draft_reply(request["messages"])


@post("/compare")
def compare(llm, data: dict) -> dict:
    return llm.chat.completions.create(model="gpt-5.5", messages=data["messages"], max_completion_tokens=1024)
