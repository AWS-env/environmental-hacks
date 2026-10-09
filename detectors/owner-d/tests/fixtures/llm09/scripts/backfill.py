"""Synthetic LLM-09 exempt path: a one-off script."""

from openai import OpenAI

client = OpenAI()
for row in ["a", "b"]:
    client.responses.create(model="gpt-4.1-mini", input=row)
