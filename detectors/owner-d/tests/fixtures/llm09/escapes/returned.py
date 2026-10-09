"""Synthetic LLM-09 exception: one response is returned whole, so a caller may read its usage."""

import anthropic

claude = anthropic.Anthropic()


def complete(prompt):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": prompt}])


def title(text):
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=16, messages=[{"role": "user", "content": text}])
    return message.content[0].text
