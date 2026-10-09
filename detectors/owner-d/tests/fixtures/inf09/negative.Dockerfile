# Synthetic INF-09 fixture: the same steps done the lean way (no findings expected).
ARG PY=3.12
FROM python:${PY} AS build
RUN apt-get update && apt-get install -y build-essential
RUN pip install --user -r requirements.txt
RUN npm install

FROM python:${PY}-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir gunicorn
RUN apk add --no-cache --virtual .build-deps gcc musl-dev \
    && pip install --no-cache-dir psycopg2 \
    && apk del .build-deps
RUN npm ci --omit=dev && npm install -g pm2
RUN yarn install --production --frozen-lockfile
RUN dnf install -y procps-ng && dnf clean all
COPY --from=build /root/.local /root/.local
CMD ["gunicorn", "app:app"]
