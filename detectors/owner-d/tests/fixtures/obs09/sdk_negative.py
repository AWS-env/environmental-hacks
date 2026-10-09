"""Synthetic fixture for OBS-09: batched SDK export (clean)."""

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class SimpleSpanProcessor:
    """A project class that happens to share the SDK name."""

    def __init__(self, exporter):
        self.exporter = exporter


def configure(tracer_provider, logger_provider):
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    return SimpleSpanProcessor(OTLPSpanExporter())
