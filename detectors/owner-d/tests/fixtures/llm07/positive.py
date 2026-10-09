# Synthetic LLM-07 fixture: LLM calls that set no output-token limit. Never executed.
import boto3
import openai
from openai import AsyncOpenAI, OpenAI

bedrock = boto3.client("bedrock-runtime", region_name="eu-west-1")
client = OpenAI()
MODEL_ID = "amazon.nova-lite-v1:0"


def summarise(text):
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": text}]}],
    )
    return response


def stream_answer(messages):
    config = {"temperature": 0.2}
    return bedrock.converse_stream(modelId=MODEL_ID, messages=messages, inferenceConfig=config)


def classify(text):
    return client.chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": text}])


def explicit_none(messages):
    return client.chat.completions.create(model="gpt-4.1-mini", messages=messages, max_tokens=None)


def respond(text):
    return client.responses.create(model="gpt-4.1", input=text)


async def draft(messages):
    async_client = AsyncOpenAI()
    return await async_client.chat.completions.create(model="gpt-4.1", messages=messages, temperature=0)


def legacy(messages):
    return openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=messages)


def injected(llm, messages):
    return llm.chat.completions.create(model="gpt-4.1", messages=messages)


class Agent:
    def __init__(self):
        self.runtime = boto3.Session().client(service_name="bedrock-runtime")

    def step(self, messages):
        request = {"modelId": MODEL_ID, "messages": messages}
        return self.runtime.converse(**request)
