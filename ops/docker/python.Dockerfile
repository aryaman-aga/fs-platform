# syntax=docker/dockerfile:1.7
# FS-Platform Python images (build context = repository root).
#   target api    : FastAPI app (also used for `migrate` and the `ai-gateway`)
#   target worker : job worker + LibreOffice headless (render fallback, ADR-0004)
#
# Supply chain (E-SEC-12): production builds pin the base image BY DIGEST, e.g.
#   --build-arg PYTHON_IMAGE=python:3.13-slim-trixie@sha256:<digest>
# (`docker buildx imagetools inspect python:3.13-slim-trixie` prints it), install from the
# firm's wheelhouse with --require-hashes, are scanned with Trivy and signed with cosign.
ARG PYTHON_IMAGE=python:3.13-slim-trixie

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC
RUN groupadd --system --gid 10001 fsapp \
 && useradd --system --uid 10001 --gid fsapp --home-dir /home/fsapp --create-home --shell /usr/sbin/nologin fsapp

# ------------------------------------------------------------------ build: dependencies into a venv
FROM base AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY fscore/ /src/fscore/
# fsweb is installed in editable mode from /app/fsweb: `fsweb.manage migrate` finds alembic.ini and the
# alembic/ scripts relative to its source tree, so the tree must exist at the same path at runtime.
COPY fsweb/ /app/fsweb/
# Optional hash-locked install: place requirements.lock (pip-compile --generate-hashes) in fsweb/ and the
# build uses it; otherwise dependencies resolve from the pyproject metadata.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && if [ -f /app/fsweb/requirements.lock ]; then /opt/venv/bin/pip install --require-hashes -r /app/fsweb/requirements.lock; fi \
 && /opt/venv/bin/pip install /src/fscore \
 && /opt/venv/bin/pip install -e /app/fsweb

# ------------------------------------------------------------------ api
FROM base AS api
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/fsweb /app/fsweb
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app/fsweb
RUN mkdir -p /work /audit-heads && chown fsapp:fsapp /work /audit-heads
USER fsapp
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"]
# The API sits on an internal network reachable only through Caddy, so forwarded headers are trusted.
CMD ["uvicorn", "fsweb.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header", "--workers", "2"]

# ------------------------------------------------------------------ worker (+ LibreOffice fallback renderer)
FROM api AS worker
USER root
# *-nogui LibreOffice + metric-compatible fonts (Carlito = Calibri, Caladea = Cambria, Liberation =
# Arial/Times) and Noto/DejaVu for the ₹ glyph, so column widths and page breaks match Excel closely.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-calc-nogui \
      fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation fonts-dejavu-core fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp SAL_USE_VCLPLUGIN=svp
USER fsapp
HEALTHCHECK NONE
CMD ["python", "-m", "fsweb.worker"]
