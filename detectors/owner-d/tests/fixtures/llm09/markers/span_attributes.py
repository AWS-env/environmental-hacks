"""Synthetic LLM-09 marker: token counts set as OpenTelemetry GenAI span attributes."""


def record(span, counts):
    span.set_attribute("gen_ai.usage.input_tokens", counts[0])
