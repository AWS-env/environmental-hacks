"""Synthetic LLM-09 boundary: only the text is printed, so the usage is still dropped."""

import anthropic

claude = anthropic.Anthropic()


def show(prompt):
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": prompt}])
    print(message.content[0].text)
