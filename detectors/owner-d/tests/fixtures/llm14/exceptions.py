# Synthetic LLM-14 fixture: legitimate exceptions and unclear data flow. Never executed.
import anthropic
from openai import OpenAI

claude = anthropic.Anthropic()
oai = OpenAI()

# Single-turn script: built once, sent once.
messages = [{"role": "user", "content": "Summarise the release notes."}]
messages.append({"role": "user", "content": "Use three bullets."})
claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def ask(question):
    history = [{"role": "user", "content": question}]
    history.append({"role": "user", "content": "Answer briefly."})
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=history)


def suppressed():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)  # noqa: LLM-14


def other_noqa():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)  # noqa: E501


class Resettable:
    def __init__(self):
        self.history = []

    def ask(self, question):
        self.history.append({"role": "user", "content": question})
        return oai.chat.completions.create(model="gpt-4.1", messages=self.history, max_tokens=100)

    def reset(self):
        self.history = []


class WithMemory:
    def __init__(self, memory):
        self.history = memory.load()

    def ask(self, question):
        self.history.append({"role": "user", "content": question})
        return oai.chat.completions.create(model="gpt-4.1", messages=self.history, max_tokens=100)


def unknown_kwargs(options):
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        oai.chat.completions.create(model="gpt-4.1", messages=messages, **options)


def closure():
    messages = []

    def add(text):
        messages.append({"role": "user", "content": text})

    while True:
        add(input())
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def stored_elsewhere(load):
    messages = load()
    while True:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


class Chat:
    def __init__(self):
        self.history = []

    def ask(self, question):
        self.history.append({"role": "user", "content": question})
        return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=self.history)


class ShortChat(Chat):
    def ask(self, question):
        del self.history[:-6]
        return super().ask(question)
