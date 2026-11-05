# Docling extractor. Build context: backend/ingest (see deploy/docker/compose.extractors.yaml).
# Models (layout, TableFormer, RapidOCR) are downloaded at BUILD time only and baked into
# /models; runtime is offline (HF_HUB_OFFLINE=1) and runs with network_mode: none.
# torch/torchvision are the CPU-only wheels (+cpu) from the PyTorch CPU index, hash-pinned.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    DOCLING_ARTIFACTS_PATH=/models \
    OMP_NUM_THREADS=2

# Runtime shared libs for opencv/torch (apt packages are not version-pinned; base image is).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY extractors/docling/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes \
        --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Bake models (network is used during build only; offline env lifted for this one step).
# docling-tools cannot pin revisions itself (they are fixed by the pinned docling version),
# so the build fails unless every baked file matches the committed sha256 manifest.
COPY extractors/docling/models.sha256 /app/models.sha256
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 HF_DATASETS_OFFLINE=0 docling-tools models download layout tableformer rapidocr -o /models \
    && (cd /models && sha256sum -c /app/models.sha256) \
    && chmod -R a+rX /models /app/models.sha256

WORKDIR /app
COPY __init__.py /app/backend/ingest/__init__.py
COPY prevalidate.py /app/backend/ingest/prevalidate.py
COPY extractors/__init__.py extractors/runner.py /app/backend/ingest/extractors/
COPY extractors/docling/__init__.py extractors/docling/extract.py /app/backend/ingest/extractors/docling/
RUN touch /app/backend/__init__.py

USER 10001:10001
ENTRYPOINT ["python", "-m", "backend.ingest.extractors.docling.extract"]
