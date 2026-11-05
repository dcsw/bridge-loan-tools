"""Spark session factory for the ingest job: local[*], Iceberg REST catalog (Lakekeeper).

The Iceberg runtime and AWS bundle jars are baked into the image under ``$SPARK_HOME/jars``
(see deploy/docker/ingest/Dockerfile), so nothing is resolved from the network at runtime.
Credentials are vended by Lakekeeper (design/ADR-0002: default for in-network clients), so no
S3 keys are configured here.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

CATALOG = "lk"


def catalog_conf(
    uri: str, warehouse: str, region: str = "us-east-1", catalog: str = CATALOG
) -> dict[str, str]:
    prefix = f"spark.sql.catalog.{catalog}"
    return {
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        prefix: "org.apache.iceberg.spark.SparkCatalog",
        f"{prefix}.type": "rest",
        f"{prefix}.uri": uri,
        f"{prefix}.warehouse": warehouse,
        f"{prefix}.header.X-Iceberg-Access-Delegation": "vended-credentials",
        f"{prefix}.client.region": region,
        f"{prefix}.rest-metrics-reporting-enabled": "false",
    }


def build_spark(app_name: str = "bridge-loan-ingest") -> SparkSession:
    from pyspark.sql import SparkSession

    conf = catalog_conf(
        uri=os.environ.get("LAKEKEEPER_URI", "http://lakekeeper:8181/catalog"),
        warehouse=os.environ.get("WAREHOUSE_NAME", "bridgeloan"),
        region=os.environ.get("AWS_REGION", "us-east-1"),
    )
    builder = (
        SparkSession.builder.master("local[*]")
        .appName(app_name)
        .config("spark.ui.enabled", "false")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.local.dir", "/tmp/spark-local")
        .config("spark.sql.warehouse.dir", "/tmp/spark-warehouse")
        .config("spark.driver.memory", os.environ.get("SPARK_DRIVER_MEMORY", "1g"))
    )
    for key, value in conf.items():
        builder = builder.config(key, value)
    return builder.getOrCreate()
