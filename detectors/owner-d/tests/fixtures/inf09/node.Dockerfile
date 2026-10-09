# Synthetic INF-09 fixture: single-stage Node.js image.
FROM node:20-alpine
WORKDIR /app
RUN apk add --virtual .gyp python3 make g++
COPY package*.json ./
RUN npm ci
COPY . .
CMD ["node", "server.js"]
