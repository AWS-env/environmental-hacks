"""Synthetic LLM-11 exceptions: unknown corpora, retries, test scopes and noqa."""

import time

from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_openai import OpenAIEmbeddings
from openai import OpenAI

client = OpenAI()
embeddings = OpenAIEmbeddings()
FIXED = [{"role": "user", "content": "List three refund rules."}]


def from_param(docs):
    return Chroma.from_documents(docs, embeddings)


def retried(items):
    for item in items:
        try:
            client.chat.completions.create(model="gpt-4o-mini", messages=FIXED)
        except Exception:
            time.sleep(1)


def stops_early(items):
    for item in items:
        client.chat.completions.create(model="gpt-4o-mini", messages=FIXED)
        break


def test_build_index():
    docs = TextLoader("fixture.txt").load()
    return Chroma.from_documents(docs, embeddings)


def suppressed():
    docs = TextLoader("policy.txt").load()
    return Chroma.from_documents(docs, embeddings)  # noqa: LLM-11


def not_suppressed():
    docs = TextLoader("terms.txt").load()
    return Chroma.from_documents(docs, embeddings)  # noqa: E501


def search(store, question):
    return store.similarity_search(question)
