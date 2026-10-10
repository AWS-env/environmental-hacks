# Synthetic LLM-08 fixture: top-tier models where the call does not look like a simple task. Never executed.
import anthropic
import boto3
from groq import Groq
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
groq = Groq()
REVIEW_PROMPT = (
    "You are a principal engineer. Review the attached design document end to end: architecture, failure "
    "modes, data model, migration plan, security and operability. Write a structured critique with "
    "concrete alternatives and the trade-offs of each."
)


def design_review(document):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=8000, system=REVIEW_PROMPT, messages=[{"role": "user", "content": document}])


def agent_step(messages):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=100, tools=[{"name": "lookup", "input_schema": {"type": "object"}}], messages=messages)


def tuned_classifier(review):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=10, output_config={"effort": "low"}, messages=[{"role": "user", "content": "Classify: " + review}])


def reasoning_classifier(review):
    return client.chat.completions.create(model="gpt-5-pro", reasoning_effort="low", max_completion_tokens=5, messages=[{"role": "user", "content": review}])


def thinking_converse(ticket):
    return bedrock.converse(modelId="anthropic.claude-opus-4-1-20250805-v1:0", messages=ticket, inferenceConfig={"maxTokens": 50}, additionalModelRequestFields={"thinking": {"type": "enabled"}})


def converse_tools(ticket):
    return bedrock.converse(modelId="anthropic.claude-opus-4-1-20250805-v1:0", messages=ticket, inferenceConfig={"maxTokens": 50}, toolConfig={"tools": []})


def other_sdk(review):
    return groq.chat.completions.create(model="claude-opus-5-5", max_tokens=5, messages=[{"role": "user", "content": "Classify: " + review}])


def unknown_body(payload):
    return bedrock.invoke_model(modelId="amazon.nova-premier-v1:0", body=payload)


def no_signal(messages):
    return client.chat.completions.create(model="o3-pro", messages=messages)
