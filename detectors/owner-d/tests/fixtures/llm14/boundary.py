# Synthetic LLM-14 fixture: loop bounds, growth placement and repeated anchors. Never executed.
import anthropic

claude = anthropic.Anthropic()
MAX_TURNS = 8


def five_turns():
    messages = []
    for _ in range(5):
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def constant_budget():
    messages = []
    for _ in range(MAX_TURNS):
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def param_budget(max_turns):
    messages = []
    for _ in range(max_turns):
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def per_question(questions):
    messages = []
    for question in questions:
        messages.append({"role": "user", "content": question})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def counted_while():
    messages = []
    turn = 0
    while turn < 3:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)
        turn += 1


def growth_before_loop_only():
    messages = []
    messages.append({"role": "user", "content": "Classify each line."})
    while True:
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)


def two_calls():
    messages = []
    while True:
        messages.append({"role": "user", "content": input()})
        claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=messages)
        claude.messages.create(model="claude-haiku-4-5", max_tokens=512, messages=messages)
