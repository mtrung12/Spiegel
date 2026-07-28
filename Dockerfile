# Production image: nginx serves the built frontend and proxies /api to
# gunicorn. Both are started by docker/entrypoint.sh, which exits if either one
# dies so the container's restart policy can act.
#
# The frontend is built into static assets here rather than served by Vite:
# `npm run dev` is a development server and is not appropriate for a
# long-running container. Use `npm run dev` on the host for local work.

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend-build

# The repo layout is mirrored, not flattened: src/i18n/index.js imports
# ../../../locales, so locales/ must sit beside frontend/ at build time.
WORKDIR /build/frontend

# Manifests first, so a source-only change does not reinstall dependencies.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY locales/ /build/locales/
COPY frontend/ ./
RUN npm run build


# ---- Stage 2: runtime ----
FROM python:3.11-slim

# nginx serves the built assets and terminates client connections; tini reaps
# the simulation subprocesses, which are spawned with start_new_session=True.
RUN apt-get update \
  && apt-get install -y --no-install-recommends nginx tini \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Python dependencies, manifests first for the same caching reason.
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen

# Backend source, plus the trees it reads at import time: config.py resolves
# PROJECT_ROOT to /app and reads config/, locale.py loads locales/.
COPY backend/ ./backend/
COPY config/ ./config/
COPY locales/ ./locales/

# The built frontend, at the path docker/nginx.conf serves as its root.
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

# Included from the stock nginx.conf's http{} block.
COPY docker/nginx.conf /etc/nginx/conf.d/spiegel.conf
RUN rm -f /etc/nginx/sites-enabled/default

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Run unprivileged. uploads/ and logs/ are the application paths written at
# runtime; nginx also needs its cache, log and pid paths owned by the same user,
# since it starts without root and cannot chown them itself.
RUN useradd --create-home --uid 10001 spiegel \
  && mkdir -p /app/backend/uploads /app/backend/logs /var/lib/nginx/body /var/log/nginx \
  && chown -R spiegel:spiegel /app/backend/uploads /app/backend/logs /var/lib/nginx /var/log/nginx \
  && touch /run/nginx.pid \
  && chown spiegel:spiegel /run/nginx.pid

USER spiegel

# Only nginx is published; gunicorn stays on 127.0.0.1:5001 behind it.
EXPOSE 3000

ENV PYTHONUNBUFFERED=1

# uv sync builds /app/backend/.venv; entrypoint.sh calls gunicorn by name.
ENV PATH="/app/backend/.venv/bin:$PATH"

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
