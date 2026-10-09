"""Synthetic LLM-09 boundary: the final streamed message (with usage) is returned to the caller."""

import anthropic

claude = anthropic.Anthropic()


def stream(prompt):
    with claude.messages.stream(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": prompt}]) as events:
        for text in events.text_stream:
            print(text)
        return events.get_final_message()


def short(prompt):
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=16, messages=[{"role": "user", "content": prompt}])
    return message.content[0].text
