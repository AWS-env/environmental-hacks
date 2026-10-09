"""Synthetic LLM-09 noqa: a suppression for another rule does not apply."""

import anthropic

claude = anthropic.Anthropic()


def ping():
    claude.messages.create(model="claude-haiku-4-5", max_tokens=1, messages=[{"role": "user", "content": "ping"}])  # noqa: E501
