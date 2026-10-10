# Synthetic LLM-08 fixture: simple tasks on models that are not top tier. Never executed.
import anthropic
import boto3
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")


def sentiment(review):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "Classify the sentiment: " + review}])


def mid_tier(review):
    return claude.messages.create(model="claude-sonnet-5-5", max_tokens=10, messages=[{"role": "user", "content": review}])


def is_spam(email):
    return client.chat.completions.create(model="gpt-5-mini", max_completion_tokens=5, messages=[{"role": "user", "content": "Answer yes or no: " + email}])


def triage(ticket):
    return bedrock.converse(modelId="amazon.nova-lite-v1:0", messages=ticket, inferenceConfig={"maxTokens": 64})
