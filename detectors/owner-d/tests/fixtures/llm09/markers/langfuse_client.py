"""Synthetic LLM-09 marker: the Langfuse drop-in OpenAI client traces every call with its usage."""

from langfuse.openai import OpenAI

client = OpenAI()
