# Synthetic LLM-02 negative: a Redis get-before-call fronts the handler (the file imports redis).
import openai
import redis
from fastapi import FastAPI

app = FastAPI()
client = openai.OpenAI()
store = redis.Redis()


def explain(topic: int):
    hit = store.get(f"explain:{topic}")
    if hit:
        return hit
    reply = client.chat.completions.create(
        model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": f"Explain step {topic}."}]
    )
    store.set(f"explain:{topic}", reply.choices[0].message.content, ex=3600)
    return reply.choices[0].message.content


@app.get("/explain/{topic}")
def explain_route(topic: int):
    return explain(topic)


@app.get("/static")
def static_prompt():
    return client.chat.completions.create(
        model="gpt-4o-mini", temperature=0, messages=[{"role": "user", "content": "Write the page footer."}]
    )
