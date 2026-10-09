# Synthetic LLM-04 fixture: the research pipeline with prompt caching configured. Never executed.
import anthropic

claude = anthropic.Anthropic()
SONNET = "claude-sonnet-4-5"
CACHE = {"type": "ephemeral"}


def research(question, document):
    messages = [{"role": "user", "content": [{"type": "text", "text": document, "cache_control": CACHE}]}]
    facts = claude.messages.create(model=SONNET, max_tokens=512, messages=messages)
    messages.append({"role": "assistant", "content": facts.content})
    messages.append({"role": "user", "content": question})
    return claude.messages.create(model=SONNET, max_tokens=512, messages=messages)
