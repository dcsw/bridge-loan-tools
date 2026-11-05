"""Spark/Iceberg/Lakekeeper smoke: create a table via the REST catalog, append, read back.

    python -m backend.ingest.smoke [table-suffix]

Prints one JSON line ``{"table": ..., "rows": [...], "spark": ..., "java": ...}``. The host
test then reads the same table with pyiceberg and DuckDB (the interoperability check that
matters, ADR-0002 addendum).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from backend.ingest.spark_session import CATALOG, build_spark

NAMESPACE = "smoke"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    name = f"spark_smoke_{args[0]}" if args else "spark_smoke"
    table = f"{CATALOG}.{NAMESPACE}.{name}"
    spark = build_spark("bridge-loan-smoke")
    try:
        spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG}.{NAMESPACE}")
        spark.sql(f"DROP TABLE IF EXISTS {table}")
        spark.sql(
            f"CREATE TABLE {table} (id INT, label STRING, ts TIMESTAMP) USING iceberg "
            "TBLPROPERTIES ('format-version'='2')"
        )
        spark.sql(
            f"INSERT INTO {table} VALUES (1,'alpha',TIMESTAMP '2024-01-02 03:04:05'),"
            "(2,'beta',TIMESTAMP '2024-01-03 03:04:05')"
        )
        spark.sql(f"INSERT INTO {table} VALUES (3,NULL,TIMESTAMP '2024-01-04 03:04:05')")
        rows = [
            {"id": r.id, "label": r.label}
            for r in spark.sql(f"SELECT id, label FROM {table} ORDER BY id").collect()
        ]
        print(
            json.dumps(
                {
                    "table": f"{NAMESPACE}.{name}",
                    "rows": rows,
                    "spark": spark.version,
                    "java": spark.sparkContext._jvm.System.getProperty("java.version"),
                }
            ),
            flush=True,
        )
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
