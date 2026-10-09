"""Synthetic LLM-09 noqa: the only dropped call is suppressed for this check."""

import anthropic

claude = anthropic.Anthropic()


def ping():
    claude.messages.create(model="claude-haiku-4-5", max_tokens=1, messages=[{"role": "user", "content": "ping"}])  # noqa: LLM-09
