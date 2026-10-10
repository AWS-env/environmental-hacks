"""Synthetic LLM-11 boundaries: trivial guards, real guards, __main__, repeats."""

import os

from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings()


def index_exists():
    return os.path.isdir("db")


def empty_check():
    docs = TextLoader("a.txt").load()
    if not docs:
        return None
    return Chroma.from_documents(docs, embeddings)


def length_check():
    chunks = TextLoader("b.txt").load_and_split()
    if len(chunks) == 0:
        raise ValueError("no chunks")
    return Chroma.from_documents(documents=chunks, embedding=embeddings)


def existence_check():
    if index_exists():
        return Chroma(persist_directory="db", embedding_function=embeddings)
    docs = TextLoader("c.txt").load()
    return Chroma.from_documents(docs, embeddings)


def twice():
    docs = TextLoader("d.txt").load()
    first = Chroma.from_documents(docs, embeddings, collection_name="a")
    second = Chroma.from_documents(docs, embeddings, collection_name="b")
    return first, second


if __name__ == "__main__":
    store = Chroma.from_documents(TextLoader("e.txt").load(), embeddings)
    print(store.as_retriever().invoke("refunds"))
