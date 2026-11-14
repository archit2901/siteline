# Web production build served by Caddy. Build from the repo root: docker build -f infra/web.Dockerfile .
# (the build copies fixtures/ into public/fixtures for the recorded demo run and the Houston grid)
FROM node:24-slim AS build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
# npm ci rejects the macOS-written lock on Linux (platform-specific optional deps); install still follows the lock
RUN npm install --no-audit --no-fund
COPY web/ ./
COPY fixtures/ /app/fixtures/
# The browser calls agent-api through Caddy at /api; the app opens in live mode
ENV VITE_AGENT_URL=/api VITE_FIXTURE_MODE=0
RUN npm run build

FROM caddy:2.10
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/web/dist /srv
