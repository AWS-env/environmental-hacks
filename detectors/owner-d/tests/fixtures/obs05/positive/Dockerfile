FROM python:3.12-slim AS base
WORKDIR /app

FROM base AS production
ENV OTEL_TRACES_SAMPLER_ARG=0.1
CMD ["python", "-m", "app"]
