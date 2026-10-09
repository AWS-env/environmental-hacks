# Synthetic LLM-13 fixture: caches with TTLs, non-LLM caches and look-alikes. Never executed.
import functools
import json
import os
from functools import lru_cache

import anthropic
import boto3
import cachetools.func
import redis
from async_lru import alru_cache
from cachetools import TTLCache, cached
from groq import Groq
from langchain_community.cache import InMemoryCache, RedisCache
from langchain_core.globals import set_llm_cache
from langchain_redis import RedisSemanticCache
from openai import AsyncOpenAI, OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
aclient = AsyncOpenAI()
bedrock = boto3.client("bedrock-runtime")
groq = Groq()
store = redis.Redis()
RESULTS = {}
EMBED_MODEL = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")

set_llm_cache(RedisCache(redis_=store, ttl=3600))
semantic = RedisSemanticCache(embeddings=None, redis_url="redis://localhost:6379", ttl=600)
unused = InMemoryCache()


@lru_cache(maxsize=None)
def get_client():
    return OpenAI()


@functools.cache
def load_prompt(name):
    with open(f"prompts/{name}.txt") as handle:
        return handle.read()


@cached(TTLCache(maxsize=256, ttl=900))
def classify(ticket):
    return client.chat.completions.create(model="gpt-5.5", max_tokens=8, messages=[{"role": "user", "content": ticket}])


@cachetools.func.ttl_cache(maxsize=128, ttl=600)
def answer(question):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=[{"role": "user", "content": question}])


@alru_cache(maxsize=64, ttl=60)
async def summarise(text):
    return await aclient.responses.create(model="gpt-5.5", max_output_tokens=200, input=text)


def cached_converse(prompt):
    hit = store.get(prompt)
    if hit is not None:
        return hit
    response = bedrock.converse(modelId="amazon.nova-lite-v1:0", messages=[{"role": "user", "content": [{"text": prompt}]}])
    store.set(prompt, json.dumps(response["output"]), ex=3600)
    return response


def cached_reply(prompt):
    hit = store.get(prompt)
    if hit is not None:
        return hit
    reply = client.responses.create(model="gpt-5.5", max_output_tokens=300, input=prompt)
    store.setex(prompt, 600, reply.output_text)
    return reply.output_text


def log_reply(prompt):
    reply = client.responses.create(model="gpt-5.5", max_output_tokens=300, input=prompt)
    store.set(f"log:{prompt}", reply.output_text)
    return reply


def collect(item_id, prompt):
    reply = client.chat.completions.create(model="gpt-5.5", max_tokens=300, messages=[{"role": "user", "content": prompt}])
    RESULTS[item_id] = reply.choices[0].message.content
    return reply


@lru_cache(maxsize=4096)
def embed(text):
    return client.embeddings.create(model="text-embedding-3-small", input=text)


@lru_cache(maxsize=4096)
def titan_embed(text):
    return bedrock.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=json.dumps({"inputText": text}))


@lru_cache(maxsize=None)
def fast_answer(question):
    return groq.chat.completions.create(model="llama-3.3-70b", messages=[{"role": "user", "content": question}])


def cached_prompt(question):
    return claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        system=[{"type": "text", "text": "You are a support agent.", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": question}],
    )


@lru_cache(maxsize=0)
def uncached(question):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, messages=[{"role": "user", "content": question}])


def _embed_one(text):
    response = bedrock.invoke_model(modelId=EMBED_MODEL, body=json.dumps({"inputText": text}))
    return json.loads(response["body"].read())["embedding"]


@lru_cache(maxsize=512)
def embed_query(text):
    return tuple(_embed_one(text))
