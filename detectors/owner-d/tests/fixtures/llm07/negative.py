# Synthetic LLM-07 fixture: bounded LLM calls and look-alikes. Never executed.
import json

import anthropic
import boto3
from groq import Groq
from openai import OpenAI
from twilio.rest import Client as TwilioClient

bedrock = boto3.client("bedrock-runtime")
client = OpenAI()
claude = anthropic.Anthropic()
groq_client = Groq()
sms = TwilioClient()
MODEL_ID = "amazon.nova-lite-v1:0"
LIMITS = {"maxTokens": 512, "temperature": 0}
EXAMPLE = "client.chat.completions.create(model='gpt-4.1', messages=m)"


def bounded_bedrock(messages, fast):
    bedrock.converse(modelId=MODEL_ID, messages=messages, inferenceConfig={"maxTokens": 256})
    bedrock.converse_stream(modelId=MODEL_ID, messages=messages, inferenceConfig=LIMITS)
    bedrock.converse(modelId=MODEL_ID, messages=messages, additionalModelRequestFields={"max_tokens": 300})
    small = {"maxTokens": 64} if fast else {"maxTokens": 512}
    return bedrock.converse(modelId=MODEL_ID, messages=messages, inferenceConfig=small)


def bounded_openai(messages):
    client.chat.completions.create(model="gpt-4.1", messages=messages, max_completion_tokens=200)
    client.chat.completions.create(model="gpt-4.1", messages=messages, max_tokens=200)
    client.chat.completions.create(model="gpt-4.1", messages=messages, extra_body={"max_tokens": 100})
    return client.responses.create(model="gpt-4.1", input=messages, max_output_tokens=300)


def out_of_scope_apis(text):
    claude.messages.create(model="claude-sonnet-4-5", max_tokens=1024, messages=text)
    sms.messages.create(body=text, to="+15550100")
    groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=text)
    client.completions.create(model="gpt-3.5-turbo-instruct", prompt=text)
    return bedrock.invoke_model(modelId="meta.llama3-8b-instruct-v1:0", body=json.dumps({"prompt": text}))


class Negotiator:
    def converse(self, topic):
        return topic


def negotiate(negotiator, topic):
    return negotiator.converse(topic)
