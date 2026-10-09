"""Synthetic LLM-09 near miss: generic tracing and CLI text, but no token usage.

The docstring mentions gen_ai.usage.input_tokens and usage, which is not telemetry.
"""

import argparse

from opentelemetry import trace

tracer = trace.get_tracer(__name__)
parser = argparse.ArgumentParser(usage="%(prog)s [question]")


def traced(handler):
    with tracer.start_as_current_span("llm-call"):
        return handler()
