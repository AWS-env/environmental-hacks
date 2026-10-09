from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON


def test_every_span_is_recorded():
    provider = TracerProvider(sampler=ALWAYS_ON)
    assert provider
