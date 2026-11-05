# Fetcher image (WU4): Playwright + Chromium baked in, non-root, no secrets.
# Build context is the repo root; see Dockerfile.dockerignore for what is sent.
# Base pinned by digest (mcr.microsoft.com/playwright/python:v1.64.1-noble). Playwright pip
# version below MUST match the browser build in the base image tag.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Versions mirror uv.lock. The base image has no python3-venv; it is a single-purpose
# container, so installing into the system python (PEP 668 override) is acceptable.
RUN python3 -m pip install --break-system-packages \
      playwright==1.64.1 boto3==1.43.117 pydantic==2.14.3

WORKDIR /app
COPY backend/common /app/backend/common
COPY backend/fetcher /app/backend/fetcher

ENV PYTHONPATH=/app \
    HOME=/tmp \
    FETCHER_DROP=/drop \
    FETCHER_CHROMIUM_SANDBOX=0

# pwuser (uid 1000) ships with the Playwright base image.
USER pwuser
ENTRYPOINT ["python", "-m", "backend.fetcher"]
