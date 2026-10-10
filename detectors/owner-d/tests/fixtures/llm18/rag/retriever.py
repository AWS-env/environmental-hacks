"""Retriever for the RAG service (synthetic LLM-18 fixture)."""

import vertexai
from langchain_aws import ChatBedrockConverse

vertexai.init(project="rag-prod", location="us-central1")
chat = ChatBedrockConverse(model="anthropic.claude-3-haiku-20240307-v1:0", region_name="eu-central-1")
