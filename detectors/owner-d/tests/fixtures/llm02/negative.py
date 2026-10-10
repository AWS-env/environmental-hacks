# Synthetic LLM-02 negatives: cached, dynamic or one-shot requests.
import datetime
import functools

import anthropic
from fastapi import FastAPI, Request

app = FastAPI()
claude = anthropic.Anthropic()

GREETING = "Write a one-line greeting for the help page."
ANSWERS = {}
history = []


@functools.lru_cache(maxsize=32)
def greeting_text():
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=50, temperature=0, messages=[{"role": "user", "content": GREETING}]
    )
    return reply.content[0].text


@app.get("/greeting")
def greeting():
    return greeting_text()


@app.get("/answer/{topic}")
def answer(topic):
    if topic in ANSWERS:
        return ANSWERS[topic]
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=200, temperature=0,
        messages=[{"role": "user", "content": f"Explain {topic}."}],
    )
    ANSWERS[topic] = reply.content[0].text
    return ANSWERS[topic]


@app.post("/ask")
async def ask(request: Request):
    body = await request.json()
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=200, temperature=0,
        messages=[{"role": "user", "content": body["question"]}],
    )
    return reply.content[0].text


@app.get("/search")
def search(q: str):
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=200, temperature=0, messages=[{"role": "user", "content": q}]
    )
    return reply.content[0].text


def handler(event, context):
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=200, temperature=0,
        messages=[{"role": "user", "content": event["body"]}],
    )
    return reply.content[0].text


@app.get("/today")
def today():
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=200, temperature=0,
        messages=[{"role": "user", "content": f"What happened on {datetime.date.today()}?"}],
    )
    return reply.content[0].text


@app.post("/chat")
def chat(message: str):
    history.append({"role": "user", "content": message})
    reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=200, temperature=0, messages=history)
    return reply.content[0].text


def agent_turn():
    messages = [{"role": "user", "content": "Plan the release checklist."}]
    messages.append({"role": "assistant", "content": "Step one."})
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=200, temperature=0, messages=messages)


@app.get("/agent")
def agent():
    return agent_turn()


def main():
    reply = claude.messages.create(
        model="claude-sonnet-4-5", max_tokens=200, temperature=0,
        messages=[{"role": "user", "content": "Write the release notes intro."}],
    )
    print(reply.content[0].text)


once = claude.messages.create(
    model="claude-sonnet-4-5", max_tokens=200, temperature=0,
    messages=[{"role": "user", "content": "Summarise the project in one line."}],
)

if __name__ == "__main__":
    main()
