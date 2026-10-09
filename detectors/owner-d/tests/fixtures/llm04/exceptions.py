# Synthetic LLM-04 fixture: legitimate exceptions, unknown requests and noqa. Never executed.
import anthropic
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
SONNET = "claude-sonnet-4-5"


def tool_continuation(messages, tools, run_tool):
    response = claude.messages.create(model=SONNET, max_tokens=512, tools=tools, messages=messages)
    messages.append({"role": "assistant", "content": response.content})
    result = run_tool(response.content[-1])
    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": result}]})
    return claude.messages.create(model=SONNET, max_tokens=512, tools=tools, messages=messages)


def alternatives(messages, short):
    if short:
        reply = client.chat.completions.create(model="gpt-4.1-mini", messages=messages)
    else:
        messages.append({"role": "user", "content": "Explain in detail."})
        reply = client.chat.completions.create(model="gpt-4.1", messages=messages)
    return reply


def fallback(messages):
    try:
        return client.chat.completions.create(model="gpt-4.1", messages=messages)
    except TimeoutError:
        messages.append({"role": "user", "content": "Be brief."})
        return client.chat.completions.create(model="gpt-4.1-mini", messages=messages)


def early_return(messages, cached):
    if cached:
        return client.chat.completions.create(model="gpt-4.1-mini", messages=messages)
    messages.append({"role": "user", "content": "Fresh answer."})
    return client.chat.completions.create(model="gpt-4.1", messages=messages)


def trimmed(history, trim):
    draft = client.chat.completions.create(model="gpt-4.1", messages=history)
    history.append({"role": "assistant", "content": draft.choices[0].message.content})
    trim(history)
    return client.chat.completions.create(model="gpt-4.1", messages=history)


def final_synthesis(document):
    notes = client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": document}])
    answer = notes.choices[0].message.content
    prompt = f"Using these notes:\n{answer}\n\nand the source:\n{document}\nwrite the final answer."
    return client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": prompt}])


def forwarded(messages, **options):
    first = client.chat.completions.create(model="gpt-4.1", messages=messages, **options)
    messages.append({"role": "assistant", "content": first.choices[0].message.content})
    return client.chat.completions.create(model="gpt-4.1", messages=messages, **options)


def compacted(messages):
    edits = {"edits": [{"type": "clear_tool_uses_20250919"}]}
    first = claude.messages.create(model=SONNET, max_tokens=512, messages=messages, context_management=edits)
    messages.append({"role": "assistant", "content": first.content})
    return claude.messages.create(model=SONNET, max_tokens=512, messages=messages, context_management=edits)


def suppressed(messages):
    first = client.chat.completions.create(model="gpt-4.1", messages=messages)
    messages.append({"role": "assistant", "content": first.choices[0].message.content})
    return client.chat.completions.create(model="gpt-4.1", messages=messages)  # noqa: LLM-04


def not_suppressed(messages):
    first = client.chat.completions.create(model="gpt-4.1", messages=messages)
    messages.append({"role": "assistant", "content": first.choices[0].message.content})
    return client.chat.completions.create(model="gpt-4.1", messages=messages)  # noqa: E501
