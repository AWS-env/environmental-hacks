"""Synthetic LLM-11 negatives: persisted indexes, per-item inputs, sampling and query-time embedding."""

import os

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader, TextLoader
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from llama_index.core import SimpleDirectoryReader, StorageContext, SummaryIndex, VectorStoreIndex, load_index_from_storage
from llama_index.core.readers import StringIterableReader
from openai import OpenAI
from tenacity import Retrying, stop_after_attempt

client = OpenAI()
embeddings = OpenAIEmbeddings()
PERSIST_DIR = "storage"
PROMPT = [{"role": "user", "content": "Summarise our refund policy."}]

# LlamaIndex persistence pattern: build once, load afterwards.
if not os.path.exists(PERSIST_DIR):
    documents = SimpleDirectoryReader("data").load_data()
    index = VectorStoreIndex.from_documents(documents)
    index.storage_context.persist(persist_dir=PERSIST_DIR)
else:
    index = load_index_from_storage(StorageContext.from_defaults(persist_dir=PERSIST_DIR))
query_engine = index.as_query_engine()


def load_or_build():
    try:
        return FAISS.load_local("faiss_index", embeddings)
    except FileNotFoundError:
        docs = DirectoryLoader("docs/").load()
        return FAISS.from_documents(docs, embeddings)


def build_if_missing():
    if os.path.isdir("db"):
        return Chroma(persist_directory="db", embedding_function=embeddings)
    docs = TextLoader("handbook.txt").load()
    return Chroma.from_documents(docs, embeddings, persist_directory="db")


SEEN = set()


def add_new_only():
    store = Chroma(persist_directory="db", embedding_function=embeddings)
    for doc in DirectoryLoader("inbox/").load():
        if doc.metadata["source"] in SEEN:
            continue
        store.add_documents([doc])


def answer(question):
    vector = client.embeddings.create(model="text-embedding-3-small", input=question)
    return vector.data[0].embedding


def summarise_each(docs):
    return [
        client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": doc}])
        for doc in docs
    ]


def summarise_loop(docs):
    out = []
    for doc in docs:
        reply = client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": doc.page_content}]
        )
        out.append(reply)
    return out


def sample_three():
    return [client.chat.completions.create(model="gpt-4o", messages=PROMPT) for _ in range(3)]


def sample_loop():
    for _ in range(3):
        client.chat.completions.create(model="gpt-4o", messages=PROMPT, temperature=1.0)


def chat(questions):
    messages = []
    for question in questions:
        messages.append({"role": "user", "content": question})
        reply = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
        messages.append(reply.choices[0].message)


def first_success(models):
    for model in models:
        reply = client.chat.completions.create(model="gpt-4o-mini", messages=PROMPT)
        if reply.choices:
            break


def with_retries():
    for attempt in Retrying(stop=stop_after_attempt(3)):
        with attempt:
            client.chat.completions.create(model="gpt-4o-mini", messages=PROMPT)


def ingest_upload(upload_path):
    docs = PyPDFLoader(upload_path).load()
    store = Chroma.from_documents(docs, embeddings)
    return store.as_retriever()


def keyword_indexes():
    docs = TextLoader("faq.txt").load()
    summary = SummaryIndex.from_documents(SimpleDirectoryReader("data").load_data())
    return BM25Retriever.from_documents(docs), summary


def build_store():
    return FAISS.from_documents(DirectoryLoader("kb/").load(), embeddings)


def get_store():
    if os.path.exists("kb_index"):
        return FAISS.load_local("kb_index", embeddings)
    return build_store()


def from_request(messages):
    texts = []
    for message in messages:
        texts.append(message)
    docs = StringIterableReader().load_data(texts=texts)
    return VectorStoreIndex.from_documents(docs)
