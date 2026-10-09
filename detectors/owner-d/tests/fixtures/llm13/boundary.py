# Synthetic LLM-13 fixture: TTL boundaries and repeated identities. Never executed.
import functools

import cachetools.func
import langchain
import redis
from async_lru import alru_cache
from langchain_community.cache import InMemoryCache, RedisCache
from langchain_core.globals import set_llm_cache
from langchain_redis import RedisCache as RedisUrlCache
from openai import AsyncOpenAI, OpenAI

client = OpenAI()
aclient = AsyncOpenAI()
store = redis.Redis()
CACHE = {}

set_llm_cache(RedisCache(store, ttl=None))
set_llm_cache(RedisUrlCache("redis://localhost:6379", 3600))
langchain.llm_cache = InMemoryCache()


def complete(prompt):
    return client.chat.completions.create(model="gpt-5.5", max_tokens=300, messages=[{"role": "user", "content": prompt}])


@functools.lru_cache(maxsize=128)
def sized(prompt):
    return complete(prompt)


@cachetools.func.ttl_cache(maxsize=128, ttl=600)
def timed(prompt):
    return complete(prompt)


@alru_cache(ttl=None)
async def no_ttl(prompt):
    return await aclient.responses.create(model="gpt-5.5", max_output_tokens=100, input=prompt)


def explicit_none(prompt):
    if store.get(prompt) is None:
        store.set(prompt, complete(prompt).choices[0].message.content, ex=None)
    return store.get(prompt)


def positional_expiry(prompt):
    if store.get(prompt) is None:
        store.set(prompt, complete(prompt).choices[0].message.content, 3600)
    return store.get(prompt)


def pipeline(prompt):
    if prompt in CACHE:
        return CACHE[prompt]
    outline = complete(f"Outline: {prompt}")
    CACHE[prompt] = outline
    CACHE[prompt + ":draft"] = complete(outline.choices[0].message.content)
    return CACHE[prompt]
