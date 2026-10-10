"""Synthetic LLM-11 exception: a one-off ingestion script that embeds, persists and exits."""

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader
from langchain_openai import OpenAIEmbeddings

docs = DirectoryLoader("docs/").load()
store = Chroma.from_documents(docs, OpenAIEmbeddings(), persist_directory="db")
print("indexed", len(docs))
