# Thin edge in front of KrakenD (ADR-0005): adds JSON bodies / Retry-After to the empty-body errors
# KrakenD CE generates. Standard library only; no pip installs.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 65532 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin edge

USER 65532:65532
WORKDIR /app
COPY deploy/docker/gateway/edge.py /app/edge.py

EXPOSE 8080
CMD ["python", "/app/edge.py"]
