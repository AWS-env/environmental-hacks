# Synthetic LLM-03 fixture: short, distinct or non-system prompts. Never executed.
import anthropic
from groq import Groq
from openai import OpenAI
from twilio.rest import Client

claude = anthropic.Anthropic()
client = OpenAI()
groq = Groq()
sms = Client()

REPEATED = """Always answer in valid JSON with the keys answer and sources.
Always answer in valid JSON with the keys answer and sources."""


def short(history):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, system="Answer briefly.", messages=history)


def short_repeats(history):
    system = "Be concise. Use British spelling. Be concise. Cite sources. Be concise."
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, system=system, messages=history)


def user_repeats(question):
    user = f"{question}\nAlways answer in valid JSON with the keys answer and sources.\nAlways answer in valid JSON with the keys answer and sources."
    messages = [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": user}]
    return client.chat.completions.create(model="gpt-5.5", messages=messages)


def no_system(history):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, messages=history)


def other_sdk(history):
    return groq.chat.completions.create(model="llama-3.3-70b", messages=[{"role": "system", "content": REPEATED}, *history])


def look_alike(to):
    return sms.messages.create(to=to, body="Your code is ready.", system=REPEATED)


def first_prompt(history):
    system = "Always answer in valid JSON with the keys answer and sources."
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, system=system, messages=history)


def second_prompt(history):
    system = "Always answer in valid JSON with the keys answer and sources."
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, system=system, messages=history)


def topics_and_modules(history):
    system = """Answer only questions about these topics:
- pension rules and retirement planning for public servants
Course modules:
- Pension Rules and Retirement Planning for Public Servants"""
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, system=system, messages=history)
