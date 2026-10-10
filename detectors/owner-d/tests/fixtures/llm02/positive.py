# Synthetic LLM-02 positives: repeatable LLM requests on repeated paths with no response cache.
import enum
import json
import time
from functools import lru_cache
from typing import Literal

import anthropic
import boto3
import openai
from fastapi import FastAPI
from flask import Flask

app = FastAPI()
web = Flask(__name__)
claude = anthropic.Anthropic()
bedrock = boto3.client("bedrock-runtime")

SYSTEM = "You are the support assistant for Example Corp. Answer in two sentences."
FAQ_PROMPT = "List our three most common billing questions with short answers."


@lru_cache(maxsize=1)
def get_client():
    return openai.OpenAI()


class Region(enum.Enum):
    EU = "eu"
    US = "us"


@app.get("/faq")
def faq():
    reply = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        temperature=0,
        system=SYSTEM,
        messages=[{"role": "user", "content": FAQ_PROMPT}],
    )
    return {"answer": reply.content[0].text}


@web.route("/explain/<topic>")
def explain(topic):
    client = get_client()
    reply = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"Explain {topic} to a new customer in one paragraph."}],
    )
    return reply.choices[0].message.content


def lambda_handler(event, context):
    reply = bedrock.converse(
        modelId="anthropic.claude-3-haiku-20240307-v1:0",
        messages=[{"role": "user", "content": [{"text": "Write today's motivational quote for the team."}]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0},
    )
    return {"statusCode": 200, "body": reply["output"]["message"]["content"][0]["text"]}


def describe_region(region: Region, tier: Literal["free", "pro"]):
    prompt = "Describe the {} plan available in {}.".format(tier, region.value)
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=300, messages=[{"role": "user", "content": prompt}]
    )
    return reply.content[0].text


@app.get("/plans/{region}")
def plans(region: Region):
    return describe_region(region, "free")


def summarise_policy():
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "temperature": 0,
        "messages": [{"role": "user", "content": "Summarise our refund policy in plain English."}],
    })
    return bedrock.invoke_model(modelId="anthropic.claude-3-haiku-20240307-v1:0", body=body)


@app.post("/policy")
def policy():
    return summarise_policy()


def poll_status():
    while True:
        reply = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=100,
            temperature=0,
            messages=[{"role": "user", "content": "Give a one-line status greeting."}],
        )
        print(reply.content[0].text)
        time.sleep(60)


def chain_client_answer(llm, page: int):
    return llm.chat.completions.create(
        model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": f"Show tip number {page}."}]
    )


@app.get("/tips/{page}")
def tips(page: int):
    return chain_client_answer(openai.OpenAI(), page)
