# Synthetic INF-09 fixture: legitimate exceptions and suppression comments.
# hadolint global ignore=DL3019
FROM node:20-bookworm-slim
ENV NODE_ENV=production PIP_NO_CACHE_DIR=1
RUN echo 'APT::Install-Recommends "false";' > /etc/apt/apt.conf.d/99norecommends
# hadolint ignore=DL3009
RUN apt-get update && apt-get install -y tini
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y curl
RUN npm ci
RUN pip install awscli
RUN apk add jq
# noqa: INF-09
RUN yarn install
RUN --mount=type=cache,target=/var/lib/apt apt-get update && apt-get install -y gcc && apt-get purge -y gcc
CMD ["node", "server.js"]
