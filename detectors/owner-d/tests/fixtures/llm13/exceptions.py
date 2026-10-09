# Synthetic LLM-13 fixture: invalidation, manual expiry, unknown values and noqa. Never executed.
import functools
import time

import anthropic
import redis
from cachetools import LRUCache, cached
from langchain_core.caches import InMemoryCache
from langchain_core.globals import set_llm_cache

claude = anthropic.Anthropic()
store = redis.Redis()
REPLIES = LRUCache(maxsize=128)
BOUNDED = {}
STAMPED = {}


def ask(question):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, messages=[{"role": "user", "content": question}])


@functools.lru_cache(maxsize=None)
def invalidated(question):
    return ask(question)


def on_docs_updated():
    invalidated.cache_clear()


@cached(REPLIES)
def cleared_store(question):
    return ask(question)


def reset():
    REPLIES.clear()


@functools.lru_cache(maxsize=256)
def bucketed(question, ttl_hash=None):
    return ask(question)


def bounded(question):
    if question not in BOUNDED:
        BOUNDED[question] = ask(question)
    return BOUNDED[question]


def forget(question):
    BOUNDED.pop(question, None)


def stamped(question):
    entry = STAMPED.get(question)
    if entry and time.time() - entry[0] < 300:
        return entry[1]
    STAMPED[question] = (time.time(), ask(question))
    return STAMPED[question][1]


def expired_later(question):
    if store.exists(question):
        return store.get(question)
    reply = ask(question)
    store.set(question, reply.content[0].text)
    store.expire(question, 600)
    return reply


def kept_ttl(question):
    if store.exists(question):
        return store.get(question)
    reply = ask(question)
    store.set(question, reply.content[0].text, keepttl=True)
    return reply


def with_options(question, **opts):
    if store.exists(question):
        return store.get(question)
    reply = ask(question)
    store.set(question, reply.content[0].text, **opts)
    return reply


def injected_store(cache, question):
    if cache.exists(question):
        return cache.get(question)
    reply = ask(question)
    cache.set(question, reply.content[0].text)
    return reply


class Assistant:
    def __init__(self):
        self._cache = {}

    def reply(self, question):
        if question not in self._cache:
            self._cache[question] = ask(question)
        return self._cache[question]


@functools.lru_cache(maxsize=None)  # noqa: LLM-13 answers depend only on the fixed glossary
def glossary(term):
    return ask(term)


@functools.lru_cache(maxsize=None)  # noqa: E501
def not_suppressed(term):
    return ask(term)


def test_cache_hit():
    set_llm_cache(InMemoryCache())
