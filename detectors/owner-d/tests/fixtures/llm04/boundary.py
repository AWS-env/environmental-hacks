# Synthetic LLM-04 fixture: smallest pipelines, final steps and scope boundaries. Never executed.
from openai import OpenAI

client = OpenAI()


def two_step_document(document):
    notes = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": document}])
    return client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": f"{notes} {document}"}])


def three_step_document(document):
    notes = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": document}])
    draft = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": f"{notes} {document}"}])
    return client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": f"{draft} {document}"}])


def outer(history):
    first = client.chat.completions.create(model="gpt-4.1", messages=history)
    history.append({"role": "assistant", "content": first.choices[0].message.content})

    def inner():
        return client.chat.completions.create(model="gpt-4.1", messages=history)

    return inner


def chat(history):
    first = client.chat.completions.create(model="gpt-4.1", messages=history)
    history.append({"role": "assistant", "content": first.choices[0].message.content})
    second = client.chat.completions.create(model="gpt-4.1", messages=history)
    history.append({"role": "assistant", "content": second.choices[0].message.content})
    return client.chat.completions.create(model="gpt-4.1", messages=history)
