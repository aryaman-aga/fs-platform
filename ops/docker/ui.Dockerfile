# syntax=docker/dockerfile:1.7
# Edge image: the built React SPA served by Caddy, which also terminates TLS and reverse-proxies
# /api to the API (build context = repository root). Caddy is rebuilt with the rate-limit module.
ARG NODE_IMAGE=node:22-bookworm-slim
ARG CADDY_BUILDER_IMAGE=caddy:2.10-builder
ARG CADDY_IMAGE=caddy:2.10

FROM ${NODE_IMAGE} AS ui-build
WORKDIR /src/fsui
COPY fsui/package.json fsui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY fsui/ ./
RUN npm run build

FROM ${CADDY_BUILDER_IMAGE} AS caddy-build
RUN xcaddy build --with github.com/mholt/caddy-ratelimit

FROM ${CADDY_IMAGE}
COPY --from=caddy-build /usr/bin/caddy /usr/bin/caddy
COPY ops/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=ui-build /src/fsui/dist /srv/fsui
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null || true
