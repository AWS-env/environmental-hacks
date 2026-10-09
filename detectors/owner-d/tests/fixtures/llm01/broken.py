# Synthetic LLM-01 fixture: malformed Python. Never executed.
def ask(client, question:
    return client.messages.create(model="claude-sonnet-4-5", max_tokens=256, system=PROMPT, messages=question)
