"""Synthetic LLM-09 exception: a response is kept on the object for later inspection."""

import boto3


class Assistant:
    def __init__(self):
        self.client = boto3.client("bedrock-runtime")
        self.last = None

    def ask(self, question):
        self.last = self.client.converse(modelId="amazon.nova-micro-v1:0", messages=[question])
        return self.last["output"]["message"]["content"][0]["text"]

    def quick(self, question):
        response = self.client.converse(modelId="amazon.nova-micro-v1:0", messages=[question])
        return response["output"]["message"]["content"][0]["text"]
