"""Synthetic LLM-09 negative: the same call, but its token usage is logged as structured fields."""

import logging

import anthropic

logger = logging.getLogger(__name__)
claude = anthropic.Anthropic()


def summarise(text):
    message = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": text}],
    )
    logger.info("llm call", extra={"model": message.model, "input_tokens": message.usage.input_tokens})
    return message.content[0].text
