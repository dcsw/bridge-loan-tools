# PyMuPDF extractor. Build context: backend/ingest (see deploy/docker/compose.extractors.yaml).
# Untrusted-PDF sandbox: non-root, no network at runtime (compose network_mode: none),
# read-only root FS, pinned hash-checked deps exported from uv.lock.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY extractors/pymupdf/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

WORKDIR /app
COPY __init__.py /app/backend/ingest/__init__.py
COPY prevalidate.py /app/backend/ingest/prevalidate.py
COPY extractors/__init__.py extractors/runner.py /app/backend/ingest/extractors/
COPY extractors/pymupdf/__init__.py extractors/pymupdf/extract.py /app/backend/ingest/extractors/pymupdf/
RUN touch /app/backend/__init__.py

USER 10001:10001
ENTRYPOINT ["python", "-m", "backend.ingest.extractors.pymupdf.extract"]
