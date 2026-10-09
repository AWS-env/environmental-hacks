FROM python:3.12-slim AS dev
ENV OTEL_TRACES_SAMPLER=always_on

FROM python:3.12-slim AS production
CMD ["python", "-m", "app"]
