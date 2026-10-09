# Synthetic OBS-01 fixture: only the dev stage logs at DEBUG.
FROM python:3.12-slim AS dev
ENV LOG_LEVEL=debug

FROM python:3.12-slim AS production
ENV LOG_LEVEL info
