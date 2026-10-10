"""Synthetic LLM-11 exception: embeddings are cached by content hash."""

from langchain.embeddings import CacheBackedEmbeddings
from langchain.storage import LocalFileStore
from langchain_community.document_loaders import DirectoryLoader
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

cached = CacheBackedEmbeddings.from_bytes_store(OpenAIEmbeddings(), LocalFileStore("./cache/"))
docs = DirectoryLoader("docs/").load()
store = FAISS.from_documents(docs, cached)
retriever = store.as_retriever()
