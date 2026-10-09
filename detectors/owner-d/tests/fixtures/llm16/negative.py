# Synthetic LLM-16 fixture: streaming handlers and code paths that serve no request. Never executed.
import json

import anthropic
import boto3
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from groq import Groq
from mylib.events import EventBus
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
groq_client = Groq()
bedrock = boto3.client("bedrock-runtime")
app = FastAPI()
bus = EventBus()
MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


@app.post("/chat")
def chat(request: dict):
    def chunks():
        with claude.messages.stream(model="claude-sonnet-4-5", max_tokens=4096, messages=request["messages"]) as stream:
            yield from stream.text_stream
    return StreamingResponse(chunks())


@app.post("/answer")
def answer(request: dict):
    return client.chat.completions.create(model="gpt-5.5", messages=request["messages"], stream=True)


@app.post("/raw")
def raw(request: dict):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=4096, messages=request["messages"], stream=True)


@app.post("/respond")
def respond(question: str):
    with client.responses.stream(model="gpt-5.5", input=question) as stream:
        return stream.get_final_response()


@app.post("/http")
def http(request: dict):
    with client.chat.completions.with_streaming_response.create(model="gpt-5.5", messages=request["messages"]) as response:
        return list(response.iter_lines())


@app.post("/summary")
def summary(messages: list):
    return bedrock.converse_stream(modelId=MODEL, messages=messages, inferenceConfig={"maxTokens": 2048})


@app.post("/invoke")
def invoke(messages: list):
    body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 8192, "messages": messages}
    return bedrock.invoke_model_with_response_stream(modelId=MODEL, body=json.dumps(body))


@app.post("/groq")
def groq_chat(request: dict):
    return groq_client.chat.completions.create(model="llama-3.3-70b", messages=request["messages"])


@app.on_event("startup")
def warm_up():
    claude.messages.create(model="claude-sonnet-4-5", max_tokens=4096, messages=[{"role": "user", "content": "hi"}])


@bus.post("report.requested")
def on_event(event):
    return client.chat.completions.create(model="gpt-5.5", messages=event["messages"])


def nightly_report(rows):
    return client.chat.completions.create(model="gpt-5.5", messages=[{"role": "user", "content": json.dumps(rows)}])


def lambda_handler(event, context):
    return bedrock.converse(modelId=MODEL, messages=event["messages"], inferenceConfig={"maxTokens": 4096})
