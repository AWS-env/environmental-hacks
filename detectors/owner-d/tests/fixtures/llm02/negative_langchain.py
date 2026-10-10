# Synthetic LLM-02 negative: the file configures a global LangChain LLM cache.
from langchain_core.globals import set_llm_cache
from langchain_core.caches import InMemoryCache
from openai import OpenAI
from fastapi import FastAPI

set_llm_cache(InMemoryCache())
app = FastAPI()
client = OpenAI()


@app.get("/motto")
def motto():
    return client.chat.completions.create(
        model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Write the company motto."}]
    )
