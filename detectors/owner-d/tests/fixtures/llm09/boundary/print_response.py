"""Synthetic LLM-09 boundary: the whole response (usage included) is printed to the logs."""

import anthropic

claude = anthropic.Anthropic()


def show(prompt):
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": prompt}])
    print(message)
