# Synthetic OBS-01 fixture: multi-stage image.
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1

FROM python:3.12-slim AS production
ENV APP_ENV=production \
    LOG_LEVEL=debug
CMD ["python", "-m", "app"]
