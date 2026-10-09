"""Synthetic OBS-05 fixture: production tracing setup."""
import os

from aws_xray_sdk.core import xray_recorder
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased

os.environ["OTEL_TRACES_SAMPLER"] = "always_on"

trace.set_tracer_provider(TracerProvider(sampler=ALWAYS_ON))

xray_recorder.configure(service="checkout", sampling=False)


def build_provider():
    sampler = ParentBased(root=TraceIdRatioBased(1.0))
    return TracerProvider(sampler=sampler)


def ratio_provider():
    return TracerProvider(TraceIdRatioBased(rate=1))


def guarded_provider(endpoint):
    if endpoint:
        return TracerProvider(sampler=ALWAYS_ON)
    return None
