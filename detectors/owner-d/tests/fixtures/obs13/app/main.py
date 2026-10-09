"""Synthetic fixture for OBS-13: probe routes excluded in the SDK."""

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

app = FastAPI()
FastAPIInstrumentor.instrument_app(
    app,
    excluded_urls="healthz,ready,actuator/health",
)


@app.get("/healthz")
def healthz():
    return {"ok": True}
