# Synthetic LLM-04 fixture: malformed Python. Never executed.
def pipeline(client, messages:
    first = client.chat.completions.create(model="gpt-4.1", messages=messages)
    return client.chat.completions.create(model="gpt-4.1", messages=messages + [first])
