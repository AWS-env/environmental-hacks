"""Synthetic LLM-09 marker: Converse usage published as a CloudWatch metric (Powertools)."""

import boto3
from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit

metrics = Metrics(namespace="Assistant")
bedrock = boto3.client("bedrock-runtime")


def answer(question):
    response = bedrock.converse(modelId="amazon.nova-micro-v1:0", messages=[{"role": "user", "content": [{"text": question}]}])
    metrics.add_metric(name="InputTokens", unit=MetricUnit.Count, value=response["usage"]["inputTokens"])
    return response["output"]["message"]["content"][0]["text"]
