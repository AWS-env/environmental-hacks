"""Synthetic fixture for OBS-09: invalid Python."""
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


def configure(provider:
    provider.add_span_processor(SimpleSpanProcessor(exporter))
