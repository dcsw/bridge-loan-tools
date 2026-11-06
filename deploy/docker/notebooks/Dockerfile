# Notebooks image (WU10): same pinned Java 17 base + Python 3.12 as the WU5b ingest image, plus the
# hash-pinned `notebooks` dependency group (jupytext, nbmake, ipykernel, pyspark, duckdb, pyarrow).
# Notebooks, backend sources and fixtures are bind-mounted read-only at run time (see compose.notebooks.yaml).
FROM eclipse-temurin:17-jre-noble@sha256:8ee89ca3f9eb6a931a2b2e8b2901a4af3554383f8077558e3631d49bb58cff1d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates procps \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH

COPY deploy/docker/notebooks/requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

RUN groupadd -g 10001 nb && useradd -u 10001 -g 10001 -M -d /tmp -s /usr/sbin/nologin nb

WORKDIR /repo
ENV HOME=/tmp \
    SPARK_LOCAL_IP=127.0.0.1 \
    PYSPARK_PYTHON=/opt/venv/bin/python \
    PYTHONPATH=/repo

USER 10001:10001
ENTRYPOINT ["python", "-m"]
CMD ["pytest", "--nbmake", "-p", "no:cacheprovider", "notebooks"]
