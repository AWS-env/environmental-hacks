"""Synthetic LLM-09 exception: the response is read by a nested function."""

import anthropic

claude = anthropic.Anthropic()


def answer(prompt):
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": prompt}])

    def text():
        return message.content[0].text

    return text()
