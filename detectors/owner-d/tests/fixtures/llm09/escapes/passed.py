"""Synthetic LLM-09 exception: a response is handed to a helper this module cannot see."""

from openai import OpenAI

from .handlers import handle

client = OpenAI()


def chat(messages):
    response = client.chat.completions.create(model="gpt-4.1-mini", messages=messages)
    return handle(response)


def label(text):
    response = client.chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": text}])
    return response.choices[0].message.content
