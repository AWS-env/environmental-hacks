# Synthetic LLM-14 fixture: histories that grow every turn and are re-sent whole. Never executed.
import json
from dataclasses import dataclass, field

import anthropic
import boto3
from openai import OpenAI

claude = anthropic.Anthropic()
oai = OpenAI()
bedrock = boto3.client("bedrock-runtime")
HISTORY = []


def chat():
    messages = []
    while True:
        messages.append({"role": "user", "content": input("> ")})
        reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=1024, messages=messages)
        messages.append({"role": "assistant", "content": reply.content})


def agent(task):
    messages = [{"role": "user", "content": task}]
    while True:
        response = oai.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "system", "content": "You are an agent."}, *messages],
        )
        if response.choices[0].finish_reason == "stop":
            return response
        messages += [response.choices[0].message, {"role": "user", "content": "continue"}]


def converse_loop(model_id):
    conversation = list()
    while True:
        conversation.append({"role": "user", "content": [{"text": input()}]})
        out = bedrock.converse(modelId=model_id, messages=conversation, inferenceConfig={"maxTokens": 512})
        conversation.append(out["output"]["message"])


def respond_loop():
    items = []
    while True:
        items = items + [{"role": "user", "content": input()}]
        oai.responses.create(model="gpt-4.1", input=items, max_output_tokens=300)


def invoke_loop(model_id):
    turns = []
    while True:
        turns.append({"role": "user", "content": input()})
        body = json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 512, "messages": turns})
        bedrock.invoke_model(modelId=model_id, body=body)


class Assistant:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.history = []

    def ask(self, question):
        self.history.append({"role": "user", "content": question})
        reply = self.client.messages.create(model="claude-sonnet-4-5", max_tokens=1024, messages=self.history)
        self.history.append({"role": "assistant", "content": reply.content})
        return reply


@dataclass
class Session:
    turns: list = field(default_factory=list)

    def send(self, text):
        self.turns.append({"role": "user", "content": text})
        return oai.chat.completions.create(model="gpt-4.1", messages=self.turns, max_tokens=200)


def handler(event, context):
    HISTORY.append({"role": "user", "content": event["body"]})
    reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=HISTORY)
    HISTORY.append({"role": "assistant", "content": reply.content})
    return reply


def grade_all(answers):
    transcript = []
    for answer in answers:
        transcript.append({"role": "user", "content": answer})
        oai.chat.completions.create(model="gpt-4.1", messages=transcript, max_tokens=50)


def continue_agent(messages):
    while True:
        reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)
        messages.append({"role": "assistant", "content": reply.content})


def injected(client, prompts):
    log = []
    while True:
        log.append({"role": "user", "content": input()})
        client.chat.completions.create(model="gpt-4.1", messages=log, max_tokens=100)
