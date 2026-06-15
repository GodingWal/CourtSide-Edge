FROM node:20-slim
WORKDIR /app
# Build toolchain for native modules (better-sqlite3 compiles via node-gyp).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 make g++ wget \
    && rm -rf /var/lib/apt/lists/*
COPY package*.json ./
RUN npm ci
COPY . .
# Container-safe default: config.ts's relative default resolves to /data inside
# the image, outside the /app/data volume mount. env can still override.
ENV DATABASE_PATH=/app/data/hoopstats_wnba.db
EXPOSE 3000
CMD ["npx", "tsx", "index.ts"]
