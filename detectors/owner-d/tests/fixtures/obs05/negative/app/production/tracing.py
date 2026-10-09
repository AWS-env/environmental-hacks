"""Synthetic OBS-05 fixture: sampled or defaulted production tracing."""
import os

import vendorlib
from aws_xray_sdk.core import xray_recorder
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ParentBased, TraceIdRatioBased

ALWAYS_ON = "on"

default_provider = TracerProvider()
ratio_provider = TracerProvider(sampler=TraceIdRatioBased(0.1))
off_provider = TracerProvider(sampler=ParentBased(ALWAYS_OFF))
vendor_provider = vendorlib.TracerProvider(sampler=ALWAYS_ON)
xray_recorder.configure(service="checkout", sampling=True)
os.environ["OTEL_TRACES_SAMPLER"] = "parentbased_traceidratio"
requested = os.getenv("OTEL_TRACES_SAMPLER", "always_on")
