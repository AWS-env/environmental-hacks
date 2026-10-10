# Synthetic LLM-02 boundaries: temperature, key types, path vs query parameters, loops and caches.
import functools

import anthropic
from fastapi import FastAPI

app = FastAPI()
claude = anthropic.Anthropic()


@app.get("/zero")
def zero():
    return claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0.0, messages=[{"role": "user", "content": "Hi."}]
    )


@app.get("/tiny")
def tiny():
    return claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0.0001, messages=[{"role": "user", "content": "Hi."}]
    )


@app.get("/unset")
def unset():
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=9, messages=[{"role": "user", "content": "Hi"}])


@app.get("/page")
def page(number: int):
    return claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0,
        messages=[{"role": "user", "content": f"Tip {number}."}],
    )


@app.get("/word")
def word(text: str):
    return claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0,
        messages=[{"role": "user", "content": f"Define {text}."}],
    )


@app.get("/items/{item}")
def item_path(item, detail):
    return claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0,
        messages=[{"role": "user", "content": f"Describe {item}."}],
    )


@app.get("/items")
def item_query(item, detail):
    return claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0,
        messages=[{"role": "user", "content": f"Describe {item}."}],
    )


def heartbeat():
    while True:
        claude.messages.create(
            model="claude-sonnet-4-5", max_tokens=50, temperature=0, messages=[{"role": "user", "content": "Ping."}]
        )


def until_ok():
    while True:
        reply = claude.messages.create(
            model="claude-sonnet-4-5", max_tokens=50, temperature=0, messages=[{"role": "user", "content": "Ping."}]
        )
        if reply:
            break


def leaf():
    return claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0, messages=[{"role": "user", "content": "Leaf."}]
    )


@functools.lru_cache(maxsize=None)
def middle():
    return leaf()


@app.get("/layered")
def layered():
    return middle()


@app.get("/twice")
def twice():
    first = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0, messages=[{"role": "user", "content": "One."}]
    )
    second = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0, messages=[{"role": "user", "content": "Two."}]
    )
    return first, second
