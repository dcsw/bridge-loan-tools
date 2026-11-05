# Unstructured extractor. Build context: backend/ingest (see deploy/docker/compose.extractors.yaml).
# Baked at BUILD time only: NLTK data, hi_res layout (yolox) + table-structure models,
# Tesseract (eng). Runtime is offline (HF_HUB_OFFLINE=1) with network_mode: none.
# torch is the CPU-only wheel (+cpu) from the PyTorch CPU index, hash-pinned.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    HF_HOME=/opt/hf \
    NLTK_DATA=/opt/nltk_data \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    UNSTRUCTURED_DISABLE_NOTICE=1 \
    OMP_NUM_THREADS=2

# Runtime system deps (apt packages are not version-pinned; the base image is).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 libmagic1 tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY extractors/unstructured/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes \
        --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Bake hi_res models at EXACT pinned Hugging Face commits (see bake_models.py; no NLTK:
# unstructured 0.27 uses no NLTK data). Network is used here only. The build then (1) loads
# both models with HF offline to prove the pinned cache is what the library resolves, and
# (2) fails unless every baked file matches models.sha256.
COPY extractors/unstructured/models.sha256 /app/models.sha256
COPY extractors/unstructured/bake_models.py /tmp/bake_models.py
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 python /tmp/bake_models.py \
    && python -c "\
from unstructured_inference.models.base import get_model; \
from unstructured_inference.models.tables import load_agent; \
get_model('yolox'); load_agent()" \
    && (cd /opt/hf/hub && sha256sum -c /app/models.sha256) \
    && rm /tmp/bake_models.py \
    && chmod -R a+rX /opt/hf /app/models.sha256

WORKDIR /app
COPY __init__.py /app/backend/ingest/__init__.py
COPY prevalidate.py /app/backend/ingest/prevalidate.py
COPY extractors/__init__.py extractors/runner.py /app/backend/ingest/extractors/
COPY extractors/unstructured/__init__.py extractors/unstructured/extract.py /app/backend/ingest/extractors/unstructured/
RUN touch /app/backend/__init__.py

USER 10001:10001
ENTRYPOINT ["python", "-m", "backend.ingest.extractors.unstructured.extract"]
