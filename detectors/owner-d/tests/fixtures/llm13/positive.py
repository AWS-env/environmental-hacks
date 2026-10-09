# Synthetic LLM-13 fixture: LLM responses cached without TTL or invalidation. Never executed.
import functools
import json

import anthropic
import boto3
import redis
from async_lru import alru_cache
from cachetools import LRUCache, cached
from langchain_community.cache import SQLiteCache
from langchain_core.caches import InMemoryCache
from langchain_core.globals import set_llm_cache
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI, OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
aclient = AsyncOpenAI()
bedrock = boto3.client("bedrock-runtime")
store = redis.Redis.from_url("redis://localhost:6379/0")
ANSWERS = {}

set_llm_cache(SQLiteCache(database_path=".langchain.db"))
chat_model = ChatOpenAI(model="gpt-5.5", cache=InMemoryCache())


@functools.lru_cache(maxsize=None)
def answer(question):
    reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=[{"role": "user", "content": question}])
    return reply.content[0].text


def ask(question):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, messages=[{"role": "user", "content": question}])


@functools.cache
def faq(topic):
    return ask(f"Explain {topic}").content[0].text


@cached(LRUCache(maxsize=256))
def classify(ticket):
    return client.chat.completions.create(model="gpt-5.5", max_tokens=8, messages=[{"role": "user", "content": ticket}])


@alru_cache
async def summarise(text):
    return await aclient.responses.create(model="gpt-5.5", max_output_tokens=200, input=text)


def cached_converse(prompt):
    key = f"llm:{prompt}"
    hit = store.get(key)
    if hit is not None:
        return json.loads(hit)
    response = bedrock.converse(modelId="amazon.nova-lite-v1:0", messages=[{"role": "user", "content": [{"text": prompt}]}])
    text = response["output"]["message"]["content"][0]["text"]
    store.set(key, json.dumps(text))
    return text


def lookup(question):
    if question in ANSWERS:
        return ANSWERS[question]
    completion = client.chat.completions.create(model="gpt-5.5", max_tokens=300, messages=[{"role": "user", "content": question}])
    ANSWERS[question] = completion.choices[0].message.content
    return ANSWERS[question]


@functools.lru_cache(maxsize=1024)
def draft(llm, prompt):
    return llm.chat.completions.create(model="gpt-5.5", max_tokens=300, messages=[{"role": "user", "content": prompt}])


class ReplyCache:
    def __init__(self):
        self.redis = redis.Redis(host="localhost", port=6379)

    def reply(self, prompt):
        hit = self.redis.hget("replies", prompt)
        if hit:
            return hit.decode()
        reply = client.responses.create(model="gpt-5.5", max_output_tokens=300, input=prompt)
        self.redis.hset(
            "replies",
            prompt,
            reply.output_text,
        )
        return reply.output_text
