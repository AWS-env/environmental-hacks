"""Synthetic LLM-11 exception: a LlamaIndex IngestionPipeline with a docstore skips unchanged documents."""

from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.storage.docstore import SimpleDocumentStore

pipeline = IngestionPipeline(transformations=[], docstore=SimpleDocumentStore())
documents = SimpleDirectoryReader("data").load_data()
nodes = pipeline.run(documents=documents)
index = VectorStoreIndex.from_documents(documents)
query_engine = index.as_query_engine()
