"""Synthetic LLM-11 exception: the LangChain indexing API skips unchanged documents."""

from langchain.indexes import SQLRecordManager, index
from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader
from langchain_openai import OpenAIEmbeddings

vectorstore = Chroma(collection_name="docs", embedding_function=OpenAIEmbeddings())
record_manager = SQLRecordManager("chroma/docs", db_url="sqlite:///record_manager_cache.sql")
docs = DirectoryLoader("docs/").load()
index(docs, record_manager, vectorstore, cleanup="incremental", source_id_key="source")
vectorstore.add_documents(docs)
retriever = vectorstore.as_retriever()
