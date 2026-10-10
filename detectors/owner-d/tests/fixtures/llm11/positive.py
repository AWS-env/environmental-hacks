"""Synthetic LLM-11 positives: whole corpora re-embedded on every run, identical requests per loop element."""

import glob
import json
import os

import boto3
from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from openai import OpenAI

client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
embeddings = OpenAIEmbeddings()

# LangChain: the app rebuilds its vector store from the docs folder at every start.
docs = DirectoryLoader("docs/", glob="**/*.md").load()
chunks = RecursiveCharacterTextSplitter(chunk_size=1000).split_documents(docs)
vectorstore = Chroma.from_documents(chunks, embeddings, persist_directory="db")
retriever = vectorstore.as_retriever()

# LlamaIndex quick start.
documents = SimpleDirectoryReader("data").load_data()
index = VectorStoreIndex.from_documents(documents)
query_engine = index.as_query_engine()


def handler(event, context):
    vectors = []
    for path in glob.glob("/opt/corpus/*.txt"):
        with open(path) as handle:
            text = handle.read()
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        vectors.append(response.data[0].embedding)
    return {"count": len(vectors)}


def sync_forever():
    while True:
        for name in os.listdir("corpus"):
            body = json.dumps({"inputText": open(os.path.join("corpus", name)).read()})
            bedrock.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=body)


def build_faq_index():
    with open("faq.txt") as handle:
        texts = handle.read().split("\n\n")
    store = FAISS.from_texts(texts, embeddings)
    return store.similarity_search("refund policy")


def summarise_all(tickets):
    summaries = []
    for ticket in tickets:
        reply = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Summarise our refund policy."}],
        )
        summaries.append(reply.choices[0].message.content)
    return summaries


def draft_all(compat_client, items):
    for item in items:
        compat_client.chat.completions.create(model="m", messages=[{"role": "user", "content": "Hello"}])
