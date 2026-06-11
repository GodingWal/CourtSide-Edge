FROM node:20-slim
WORKDIR /app
# Build toolchain for native modules (better-sqlite3 compiles via node-gyp).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 make g++ \
    && rm -rf /var/lib/apt/lists/*
COPY package*.json ./
RUN npm ci
COPY . .
EXPOSE 3000
CMD ["npx", "tsx", "index.ts"]
