"""Synthetic LLM-09 exempt path: a test that calls the API directly."""

import anthropic


def test_summary_mentions_topic():
    claude = anthropic.Anthropic()
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": "hi"}])
    assert message.content[0].text
