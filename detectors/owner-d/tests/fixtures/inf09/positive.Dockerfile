# syntax=docker/dockerfile:1
# Synthetic INF-09 fixture: shipped stages that put avoidable bytes in the image.
FROM golang:1.22 AS tools
RUN apt-get update && apt-get install -y git
RUN go build -o /out/tool ./cmd/tool

FROM python:3.12 AS base
RUN apt-get update \
    && apt-get install -y \
       build-essential \
       libpq-dev
RUN python -m pip install --upgrade pip \
    && pip3 install -r requirements.txt

FROM base AS runtime
COPY --from=tools /out/tool /usr/local/bin/tool
CMD ["python", "app.py"]
