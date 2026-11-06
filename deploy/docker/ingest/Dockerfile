# Ingest image (WU5b): Spark 4.0 (local[*]) + Iceberg 1.12 runtime, Java 17, Python 3.12.
# Build context is the repo root (see Dockerfile.dockerignore). No network is needed at runtime:
# the Iceberg jars are baked in (sha256-verified) and Spark never resolves packages.
# Pairing is recorded in docs/adr/0002-version-pins.md ("Spark / Iceberg / Java / Python").
FROM eclipse-temurin:17-jre-noble@sha256:8ee89ca3f9eb6a931a2b2e8b2901a4af3554383f8077558e3631d49bb58cff1d

ARG ICEBERG_VERSION=1.12.0
ARG ICEBERG_SPARK_SHA256=3f50e93e8ed322ae0da0e380f1b6c27116404329c62e55f0586697b1b8378306
ARG ICEBERG_AWS_SHA256=dd7e49ffa4ac9b215bed846581991259e3aec9bb2fa08d6cec029c46cd66d957

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Ubuntu noble ships Python 3.12 (matches requires-python). procps: Spark launcher uses ps.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates procps \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH

# Hash-pinned deps exported from uv.lock (group `spark`: pyspark + pydantic).
COPY deploy/docker/ingest/requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Iceberg jars straight into Spark's classpath so no dependency resolution happens at runtime.
RUN SPARK_JARS="$(python -c 'import pyspark,os;print(os.path.join(os.path.dirname(pyspark.__file__),"jars"))')" \
 && cd "$SPARK_JARS" \
 && curl -fsSLO "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/${ICEBERG_VERSION}/iceberg-spark-runtime-4.0_2.13-${ICEBERG_VERSION}.jar" \
 && curl -fsSLO "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-aws-bundle/${ICEBERG_VERSION}/iceberg-aws-bundle-${ICEBERG_VERSION}.jar" \
 && echo "${ICEBERG_SPARK_SHA256}  iceberg-spark-runtime-4.0_2.13-${ICEBERG_VERSION}.jar" | sha256sum -c - \
 && echo "${ICEBERG_AWS_SHA256}  iceberg-aws-bundle-${ICEBERG_VERSION}.jar" | sha256sum -c -

# Hadoop/Ivy need a passwd entry for the runtime uid (else user name/home are null).
RUN groupadd -g 10001 ingest && useradd -u 10001 -g 10001 -M -d /tmp -s /usr/sbin/nologin ingest

WORKDIR /app
COPY backend/common /app/backend/common
COPY backend/ingest/__init__.py backend/ingest/bronze.py backend/ingest/quarantine.py \
     backend/ingest/spark_session.py backend/ingest/smoke.py \
     backend/ingest/unify.py backend/ingest/loans.py backend/ingest/geocode.py \
     backend/ingest/views.py backend/ingest/silver_job.py /app/backend/ingest/
COPY backend/ingest/sql /app/backend/ingest/sql
COPY config/classifier.yaml /app/config/classifier.yaml
RUN touch /app/backend/__init__.py

ENV PYTHONPATH=/app \
    HOME=/tmp \
    SPARK_LOCAL_IP=127.0.0.1 \
    PYSPARK_PYTHON=/opt/venv/bin/python

USER 10001:10001
ENTRYPOINT ["python", "-m"]
CMD ["backend.ingest.bronze"]
