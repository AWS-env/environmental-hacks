"""Synthetic fixture for OBS-09: SDK set-up that exports spans and log records one by one."""

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor as LogProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace import export

provider = TracerProvider()
provider.add_span_processor(export.SimpleSpanProcessor(OTLPSpanExporter(endpoint="https://otlp.example.com:4317")))
trace.set_tracer_provider(provider)


def configure_logging():
    log_exporter = OTLPLogExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(
        LogProcessor(
            log_exporter,
        )
    )
    set_logger_provider(logger_provider)
