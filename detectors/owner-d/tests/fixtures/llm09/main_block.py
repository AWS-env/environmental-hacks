"""Synthetic LLM-09 exception: the only call runs under `if __name__ == "__main__"`."""

import anthropic

if __name__ == "__main__":
    claude = anthropic.Anthropic()
    message = claude.messages.create(model="claude-haiku-4-5", max_tokens=64, messages=[{"role": "user", "content": "hi"}])
    print(message.content[0].text)
