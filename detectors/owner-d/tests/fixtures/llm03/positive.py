# Synthetic LLM-03 fixture: bloated or redundant system prompts. Never executed.
import json
import textwrap

import anthropic
import boto3
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

SUPPORT_PROMPT = textwrap.dedent("""
    You are a support assistant for Acme Cloud.
    - Always answer in valid JSON with the keys answer and sources.
    - Keep answers under 120 words.
    1. Cite the help-center article you used for every answer.
    **IMPORTANT:** always answer in valid JSON with the keys answer and sources!
""")

POLICY = (
    "You are the travel desk assistant for a mid-sized engineering company with offices in four countries. "
    "Book economy class for flights shorter than six hours and premium economy for longer flights. "
    "Prefer direct flights when the price difference is less than one hundred and fifty euros. "
    "Hotels must be within two kilometres of the office the traveller is visiting this week. "
    "Ask for manager approval when the total trip cost is more than two thousand five hundred euros. "
    "Do not book refundable fares unless the traveller explicitly asks for a flexible ticket. "
    "Always list the carrier, departure time, arrival time and total price for each option. "
    "Offer at most three options, ordered by total price and then by total travel time. "
    "When a train journey takes less than four hours, offer the train before any flight option."
)

RULES = """Summarise the incident report for the on-call engineer.
State the customer impact in the first sentence of the summary.
Never include customer names or account numbers in the summary.
Again, never include customer names or account numbers in the summary."""


def support(history):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, system=SUPPORT_PROMPT, messages=history)


def plan_trip(question, user_name):
    system = f"""You help {user_name} plan business trips.
Recommend trains over flights for journeys under five hundred kilometres.
Mention the total travel time for every option you list.

Recommend trains over flights for journeys under five hundred kilometres.
Mention the total travel time for every option you list."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    return client.chat.completions.create(model="gpt-5.5", messages=messages)


def book(request):
    return client.responses.create(model="gpt-5.5", instructions=POLICY, input=request)


def summarise(report):
    developer = {"role": "developer", "content": [{"type": "text", "text": RULES}]}
    return client.chat.completions.create(model="gpt-5.5", messages=[developer, {"role": "user", "content": report}])


def converse(messages):
    return bedrock.converse(
        modelId=BEDROCK_MODEL,
        system=[
            {"text": "Translate the user's message into formal business German."},
            {"text": "Translate the user's message into formal business German. Keep product names in English."},
        ],
        messages=messages,
    )


def invoke(messages):
    body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 256, "system": RULES, "messages": messages}
    return bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(body))


def injected(llm, report):
    return llm.chat.completions.create(model="gpt-5.5", messages=[{"role": "system", "content": RULES}, {"role": "user", "content": report}])
