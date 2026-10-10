# Synthetic LLM-14 fixture: bounded or non-growing histories. Never executed.
from collections import deque

import anthropic
from groq import Groq
from openai import OpenAI

claude = anthropic.Anthropic()
oai = OpenAI()
groq = Groq()
SYSTEM = {"role": "system", "content": "Be brief."}
MAX_TURNS = 20


def window():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages[-10:])


def system_plus_window():
    history = []
    while True:
        history.append({"role": "user", "content": input()})
        oai.chat.completions.create(model="gpt-4.1", messages=[SYSTEM] + history[-6:], max_tokens=100)


def bounded_deque():
    recent = deque(maxlen=20)
    while True:
        recent.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=list(recent))


def pop_oldest():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        if len(messages) > 30:
            messages.pop(0)
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def delete_oldest():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        del messages[:-12]
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def stop_when_long():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)
        if len(messages) >= MAX_TURNS:
            break


def trimmed(trim):
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        trim(messages, max_tokens=8000)
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def summarised(summarise):
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)
        messages = [{"role": "user", "content": summarise(messages, reply)}]


def server_side_truncation():
    items = []
    while True:
        items.append({"role": "user", "content": input()})
        oai.responses.create(model="gpt-4.1", input=items, truncation="auto", max_output_tokens=200)


def fresh_each_turn():
    while True:
        messages = [{"role": "user", "content": input()}]
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def cleared():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)
        messages.clear()


def other_sdk():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        groq.chat.completions.create(model="llama-3.3-70b", messages=messages)


class Windowed:
    def __init__(self):
        self.history = []

    def ask(self, question):
        self.history.append({"role": "user", "content": question})
        self.history = self.history[-8:]
        return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=self.history)
