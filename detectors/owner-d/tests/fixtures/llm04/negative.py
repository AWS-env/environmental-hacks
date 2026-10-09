# Synthetic LLM-04 fixture: steps that pass a slice, a summary or only the previous output. Never executed.
import anthropic
import boto3
from groq import Groq
from openai import OpenAI
from twilio.rest import Client as TwilioClient

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
groq_client = Groq()
sms = TwilioClient()
SONNET = "claude-sonnet-4-5"


def single_call(messages):
    messages.append({"role": "user", "content": "Answer briefly."})
    return claude.messages.create(model=SONNET, max_tokens=256, messages=messages)


def sliced(history):
    first = client.chat.completions.create(model="gpt-4.1", messages=history)
    history.append({"role": "assistant", "content": first.choices[0].message.content})
    return client.chat.completions.create(model="gpt-4.1", messages=history[-4:])


def summarised(history):
    summary = client.chat.completions.create(model="gpt-4.1", messages=history + [{"role": "user", "content": "Summarise."}])
    history = [{"role": "user", "content": summary.choices[0].message.content}]
    history.append({"role": "user", "content": "Plan the next step."})
    return client.chat.completions.create(model="gpt-4.1", messages=history)


def output_only(document):
    facts = claude.messages.create(model=SONNET, max_tokens=512, messages=[{"role": "user", "content": document}])
    themes = claude.messages.create(model=SONNET, max_tokens=512, messages=[{"role": "user", "content": facts.content}])
    return claude.messages.create(model=SONNET, max_tokens=512, messages=[{"role": "user", "content": themes.content}])


def same_request_two_models(messages):
    fast = client.chat.completions.create(model="gpt-4.1-mini", messages=messages)
    strong = client.chat.completions.create(model="gpt-4.1", messages=messages)
    return fast, strong


def short_inputs(question, topic):
    plan = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": f"Plan: {question}"}])
    step = plan.choices[0].message.content
    answer = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": f"{step} {question}"}])
    return client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": f"{answer} {topic}"}])


def excerpt(document, ask):
    outline = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": document}])
    points = outline.choices[0].message.content
    detail = client.chat.completions.create(
        model="gpt-4.1", messages=[{"role": "user", "content": f"{points}\n{document[:2000]}"}]
    )
    return client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": f"{detail} {ask}"}])


def other_sdks(history):
    groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=history)
    history.append({"role": "user", "content": "More."})
    groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=history)
    sms.messages.create(body=str(history), to="+15550100")
    return sms.messages.create(body=str(history), to="+15550101")
