# Synthetic fixture for OBS-12: shipped image launched through opentelemetry-instrument.
FROM python:3.12-slim AS build
RUN pip install --no-cache-dir opentelemetry-distro opentelemetry-exporter-otlp \
    && opentelemetry-bootstrap -a install
CMD ["opentelemetry-instrument", "python", "build_check.py"]

FROM build
COPY app.py /app/app.py
ENV OTEL_SERVICE_NAME=billing
CMD ["opentelemetry-instrument", \
     "python", "/app/app.py"]
