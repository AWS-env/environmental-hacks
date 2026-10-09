# Synthetic LLM-07 fixture: unresolvable configuration, managed prompts and noqa. Never executed.
import boto3
from openai import OpenAI

bedrock = boto3.client("bedrock-runtime")
client = OpenAI()
MODEL_ID = "amazon.nova-lite-v1:0"


def forwarded(messages, **options):
    return client.chat.completions.create(model="gpt-4.1", messages=messages, **options)


def config_from_caller(messages, inference_config):
    return bedrock.converse(modelId=MODEL_ID, messages=messages, inferenceConfig=inference_config)


def mutated(messages):
    config = {"temperature": 0}
    config["maxTokens"] = 256
    return bedrock.converse(modelId=MODEL_ID, messages=messages, inferenceConfig=config)


def managed_prompt(prompt_arn, variables):
    return bedrock.converse(modelId=prompt_arn, promptVariables=variables)


def managed_prompt_literal():
    return bedrock.converse(modelId="arn:aws:bedrock:eu-west-1:111122223333:prompt/PROMPT12345:1")


def stored_prompt(text):
    return client.responses.create(prompt={"id": "pmpt_123"}, input=text)


def suppressed(messages):
    return client.chat.completions.create(model="gpt-4.1", messages=messages)  # noqa: LLM-07


def not_suppressed(messages):
    return client.chat.completions.create(model="gpt-4.1", messages=messages)  # noqa: E501
