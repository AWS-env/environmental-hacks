# Synthetic LLM-07 fixture: repeated anchors and a shadowed client name. Never executed.
import httpx
from openai import OpenAI

client = OpenAI()


def fetch(url):
    with httpx.Client() as client:
        return client.get(url)


def pipeline(messages):
    first = client.chat.completions.create(model="gpt-4.1", messages=messages)
    return client.with_options(timeout=30).chat.completions.create(model="gpt-4.1", messages=[first])
