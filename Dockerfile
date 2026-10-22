# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS web
WORKDIR /build
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
COPY desktop/package.json desktop/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY desktop/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    PYTHONUTF8=1 \
    FORGEAGENT_HOST=0.0.0.0 \
    FORGEAGENT_PORT=8765 \
    FORGEAGENT_DATA_DIR=/data \
    FORGEAGENT_WORKSPACE_DIR=/workspace \
    FORGEAGENT_USER_SKILLS_DIR=/data/user-skills \
    FORGEAGENT_WEB_DIR=/app/web \
    FORGEAGENT_SANDBOX_BACKEND=auto

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bubblewrap \
        ca-certificates \
        curl \
        git \
        libstdc++6 \
        tzdata \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# Reuse the exact Node runtime from the builder so npx-based MCP servers work.
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/forge_agent ./forge_agent
COPY --from=web /build/dist ./web

RUN useradd --uid 1000 --create-home --shell /bin/bash forgeagent \
    && mkdir -p /data /workspace \
    && chown -R forgeagent:forgeagent /data /workspace
USER 1000:1000

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).read()"

CMD ["python", "-m", "forge_agent.server"]
