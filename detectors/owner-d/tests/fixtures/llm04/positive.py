# Synthetic LLM-04 fixture: pipeline steps that receive the whole context again. Never executed.
import json

import anthropic
import boto3
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
SONNET = "claude-sonnet-4-5"
NOVA = "amazon.nova-lite-v1:0"


def research(question, document):
    messages = [{"role": "user", "content": f"Read this report:\n{document}\n\nList the key facts."}]
    facts = claude.messages.create(model=SONNET, max_tokens=512, messages=messages)
    messages.append({"role": "assistant", "content": facts.content})
    messages.append({"role": "user", "content": "Group the facts by theme."})
    themes = claude.messages.create(model=SONNET, max_tokens=512, messages=messages)
    messages.extend([{"role": "assistant", "content": themes.content}, {"role": "user", "content": question}])
    return claude.messages.create(model=SONNET, max_tokens=512, messages=messages)


def draft_and_critique(history, topic):
    draft = client.chat.completions.create(model="gpt-4.1", messages=history + [{"role": "user", "content": topic}])
    critique = client.chat.completions.create(
        model="gpt-4.1",
        messages=history + [{"role": "user", "content": "Critique the draft."}],
    )
    return draft, critique


def converse_pipeline(conversation, ask):
    outline = bedrock.converse(modelId=NOVA, messages=conversation)
    conversation.append(outline["output"]["message"])
    return bedrock.converse(modelId=NOVA, messages=[*conversation, {"role": "user", "content": [{"text": ask}]}])


def respond_twice(history):
    first = client.responses.create(model="gpt-4.1", input=history)
    history += [{"role": "assistant", "content": first.output_text}, {"role": "user", "content": "Shorter."}]
    return client.responses.create(model="gpt-4.1", input=history)


def contract_review(contract_text):
    clauses_prompt = f"Extract every clause from this contract:\n{contract_text}"
    clauses = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": clauses_prompt}])
    clause_list = clauses.choices[0].message.content
    risk_prompt = f"Rate the risk of each clause:\n{clause_list}\n\nFull contract:\n{contract_text}"
    risks = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": risk_prompt}])
    return client.chat.completions.create(
        model="gpt-4.1", messages=[{"role": "user", "content": "Summarise: " + risks.choices[0].message.content}]
    )


def invoke_pipeline(chat_log):
    body = json.dumps({"messages": chat_log, "max_tokens": 256})
    summary = bedrock.invoke_model(modelId="anthropic.claude-3-haiku-20240307-v1:0", body=body)
    chat_log.append({"role": "assistant", "content": summary["body"].read().decode()})
    request = json.dumps({"messages": chat_log, "max_tokens": 256})
    return bedrock.invoke_model(modelId="anthropic.claude-3-haiku-20240307-v1:0", body=request)


def injected(llm, messages):
    plan = llm.chat.completions.create(model="gpt-4.1", messages=messages)
    messages.append({"role": "assistant", "content": plan.choices[0].message.content})
    return llm.chat.completions.create(model="gpt-4.1", messages=messages)
