"""Synthetic LLM-09 exception: the whole response is serialized (usage included) and cached."""

import anthropic

claude = anthropic.Anthropic()
CACHE = {}


def cached(prompt):
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": prompt}])
    CACHE[prompt] = message.model_dump_json()
    return message.content[0].text
