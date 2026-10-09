"""Synthetic LLM-09 marker: OpenTelemetry botocore instrumentation records gen_ai.usage.* on Bedrock spans."""

from opentelemetry.instrumentation.botocore import BotocoreInstrumentor

BotocoreInstrumentor().instrument()
