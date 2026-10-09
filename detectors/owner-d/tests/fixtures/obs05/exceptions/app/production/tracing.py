"""Synthetic OBS-05 fixture: samplers chosen at runtime."""
import os

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, TraceIdRatioBased

if os.getenv("TRACE_EVERYTHING"):
    sampler = ALWAYS_ON
else:
    sampler = TraceIdRatioBased(0.1)
branch_provider = TracerProvider(sampler=sampler)

chosen = TracerProvider(sampler=ALWAYS_ON if os.getenv("DEBUG_TRACING") else TraceIdRatioBased(0.1))

if os.getenv("TRACE_EVERYTHING"):
    switched = TracerProvider(sampler=ALWAYS_ON)
else:
    switched = TracerProvider(sampler=TraceIdRatioBased(0.1))


def make(sampler=ALWAYS_ON):
    return TracerProvider(sampler=sampler)


def reassigned():
    current = ALWAYS_ON
    current = TraceIdRatioBased(0.1)
    return TracerProvider(sampler=current)
