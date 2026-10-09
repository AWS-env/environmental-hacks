"""Synthetic LLM-09 positive: responses are read for their text only, so token usage is dropped."""

import anthropic
from openai import OpenAI

claude = anthropic.Anthropic()
oai = OpenAI()


def summarise(text):
    message = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": text}],
    )
    return message.content[0].text


def stream_answer(question):
    stream = oai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": question}],
        stream=True,
    )
    return "".join(chunk.choices[0].delta.content or "" for chunk in stream)


def warm_up():
    oai.responses.create(model="gpt-4.1-mini", input="ping")


async def draft(client: anthropic.AsyncAnthropic, prompt):
    request = {"model": "claude-haiku-4-5", "max_tokens": 256}
    async with client.messages.stream(messages=[{"role": "user", "content": prompt}], **request) as stream:
        async for text in stream.text_stream:
            print(text, end="")
        final = await stream.get_final_message()
    return final.content[0].text
