# Synthetic LLM-08 fixture: top-tier models hard-coded for simple tasks. Never executed.
import json

import anthropic
import boto3
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
TRIAGE_MODEL = "us.anthropic.claude-opus-4-1-20250805-v1:0"
SENTIMENT_PROMPT = "Classify the sentiment of the review as positive, negative or neutral."


def sentiment(review):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=10, system=SENTIMENT_PROMPT, messages=[{"role": "user", "content": review}])


def is_spam(email):
    messages = [{"role": "user", "content": f"Is this email spam? Answer yes or no.\n\n{email}"}]
    return client.chat.completions.create(model="gpt-5-pro", messages=messages, max_completion_tokens=5)


def ticket_category(ticket):
    return client.responses.create(model="o3-pro", instructions="Categorize the support ticket into billing, bug or feature.", input=ticket)


def triage(ticket):
    return bedrock.converse(
        modelId=TRIAGE_MODEL,
        messages=[{"role": "user", "content": [{"text": ticket}]}],
        inferenceConfig={"maxTokens": 64, "temperature": 0},
    )


def order_number(email):
    body = {"schemaVersion": "messages-v1", "messages": [{"role": "user", "content": [{"text": "Extract the order number from this email: " + email}]}], "inferenceConfig": {"max_new_tokens": 20}}
    return bedrock.invoke_model(modelId="amazon.nova-premier-v1:0", body=json.dumps(body))


def language(llm, text):
    return llm.chat.completions.create(model="gpt-5.5-pro", messages=[{"role": "user", "content": text}], max_tokens=8)
