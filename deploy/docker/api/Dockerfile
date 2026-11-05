# API image: fastapi + duckdb only (+ uvicorn). Build context is the repo root.
# DuckDB's httpfs extension is baked in here; the running API never INSTALLs or autoloads anything.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/home/app

# Versions match uv.lock (tests/api/test_image_deps.py fails on drift).
RUN pip install --no-cache-dir fastapi==0.162.0 duckdb==2.0.0 uvicorn==0.54.0

RUN useradd --uid 65532 --no-create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /home/app /var/lib/bridge-loan/audit \
    && chown -R 65532:65532 /home/app /var/lib/bridge-loan

USER 65532:65532

RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"

WORKDIR /app
COPY backend/api /app/backend/api
COPY backend/common/__init__.py backend/common/models.py backend/common/logging.py /app/backend/common/

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.api.main:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
