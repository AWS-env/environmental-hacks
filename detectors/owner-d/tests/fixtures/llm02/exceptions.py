# Synthetic LLM-02 exceptions: intended variety, LLM-11 loops, embeddings, streaming, tests and noqa.
import json

import boto3
import openai
from fastapi import FastAPI

app = FastAPI()
client = openai.OpenAI()
bedrock = boto3.client("bedrock-runtime")
CREATIVITY = float("0.7")
TOPICS = ["billing", "shipping", "returns"]


@app.get("/idea")
def idea():
    return client.chat.completions.create(
        model="gpt-4o-mini", temperature=0.9, messages=[{"role": "user", "content": "Suggest a team lunch idea."}]
    )


@app.get("/names")
def names():
    return client.chat.completions.create(
        model="gpt-4o-mini", n=3, messages=[{"role": "user", "content": "Suggest a project codename."}]
    )


@app.get("/slogan")
def slogan():
    return client.chat.completions.create(
        model="gpt-4o-mini", temperature=CREATIVITY, messages=[{"role": "user", "content": "Write a slogan."}]
    )


@app.get("/digest")
def digest():
    replies = []
    for _topic in TOPICS:
        replies.append(client.chat.completions.create(
            model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Write the digest header."}]
        ))
    return replies


@app.get("/embedding")
def embedding():
    return bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0", body=json.dumps({"inputText": "Example Corp support"})
    )


@app.get("/stream")
def stream():
    return client.chat.completions.create(
        model="gpt-4o-mini", temperature=0, stream=True,
        messages=[{"role": "user", "content": "Write the onboarding intro."}],
    )


@app.get("/converse-stream")
def converse_stream():
    return bedrock.converse_stream(
        modelId="anthropic.claude-3-haiku-20240307-v1:0",
        messages=[{"role": "user", "content": [{"text": "Write the onboarding intro."}]}],
        inferenceConfig={"temperature": 0},
    )


def test_handler_prompt():
    @app.get("/test-only")
    def inner_route():
        return client.chat.completions.create(
            model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Say hi."}]
        )
    return inner_route


@app.get("/legal")
def legal():
    return client.chat.completions.create(  # noqa: LLM-02
        model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Write the legal footer."}]
    )


@app.get("/footer")
def footer():
    return client.chat.completions.create(  # noqa: E501
        model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Write the page footer."}]
    )


@app.get("/prompt-cached")
def prompt_cached():
    return client.chat.completions.create(
        model="gpt-4o-mini", temperature=0, prompt_cache_key="footer-v1",
        messages=[{"role": "user", "content": "Write the page header."}],
    )


@app.get("/health")
def health_check():
    client.chat.completions.create(
        model="gpt-4o-mini", temperature=0, max_tokens=1, messages=[{"role": "user", "content": "test"}]
    )
    return {"status": "ok"}
