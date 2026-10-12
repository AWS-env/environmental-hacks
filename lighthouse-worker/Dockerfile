FROM node:22-bookworm

RUN apt-get update && apt-get install -y \
    chromium \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_PATH=/usr/bin/chromium

WORKDIR /app

COPY package*.json ./

RUN npm install

COPY server.js ./

EXPOSE 3000

CMD ["node", "server.js"]
