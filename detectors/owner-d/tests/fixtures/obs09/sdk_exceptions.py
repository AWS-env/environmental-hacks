"""Synthetic fixture for OBS-09: SDK set-ups that are not flagged, except the last one."""

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

exporter = OTLPSpanExporter()


def console(provider):
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))


def injected(provider, exporter):
    provider.add_span_processor(SimpleSpanProcessor(exporter))


def suppressed(provider):
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))  # noqa: OBS-09


def other_code(provider):
    # noqa: E501
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
