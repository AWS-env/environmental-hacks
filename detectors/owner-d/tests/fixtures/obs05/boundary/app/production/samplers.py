from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

int_rate = TracerProvider(sampler=TraceIdRatioBased(1))
float_rate = TracerProvider(sampler=TraceIdRatioBased(1.0))
string_rate = TracerProvider(sampler=TraceIdRatioBased("1"))
bool_rate = TracerProvider(sampler=TraceIdRatioBased(True))
below = TracerProvider(sampler=TraceIdRatioBased(0.99))
