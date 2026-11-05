"""raw-ingest: drop dir -> content-addressed raw store (MinIO ``raw`` bucket) + manifest rows.

Runs OUTSIDE the isolated fetch network (it needs MinIO). Layout in the ``raw`` bucket:

* ``pdf/<sha256>.pdf``        immutable PDF bytes (key = doc_id)
* ``manifest/<doc_id>.json``  one WU2 ``Manifest`` row per doc_id (first writer wins)

A repeated sha256 is a no-op: the object and the manifest row already exist. Drop files are
archived to ``ingested/`` (or ``rejected/`` if invalid) so re-runs are cheap.

CLI: ``python -m backend.fetcher.raw_ingest [--drop data/drop]`` with ``MINIO_ROOT_USER`` /
``MINIO_ROOT_PASSWORD`` (and optional ``MINIO_ENDPOINT``) in the environment.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError

from backend.common.models import Manifest

PDF_PREFIX = "pdf/"
MANIFEST_PREFIX = "manifest/"
NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}

log = logging.getLogger(__name__)


@dataclass
class IngestSummary:
    uploaded: int = 0
    duplicates: int = 0
    gaps: int = 0
    rejected: int = 0


class RawIngestor:
    def __init__(self, s3: Any, *, bucket: str = "raw") -> None:
        self._s3 = s3
        self._bucket = bucket

    def _exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in NOT_FOUND_CODES:
                return False
            raise
        return True

    def _put(self, key: str, body: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=body)

    @staticmethod
    def _archive(drop_dir: Path, name: str, files: list[Path], sub: str) -> None:
        target = drop_dir / sub
        target.mkdir(exist_ok=True)
        for path in files:
            if path.exists():
                os.replace(path, target / path.name)
        log.info("drop file %s -> %s/", name, sub)

    def ingest_dir(self, drop_dir: Path) -> IngestSummary:
        summary = IngestSummary()
        drop_dir = Path(drop_dir)
        if not drop_dir.is_dir():
            return summary
        for row_path in sorted(drop_dir.glob("*.json")):
            pdf_path = row_path.with_suffix(".pdf")
            row_bytes = row_path.read_bytes()
            try:
                row = Manifest.model_validate_json(row_bytes)
            except ValidationError:
                summary.rejected += 1
                self._archive(drop_dir, row_path.name, [row_path, pdf_path], "rejected")
                continue
            if row.status == "fetched" and not self._pdf_is_valid(row, pdf_path):
                summary.rejected += 1
                self._archive(drop_dir, row_path.name, [row_path, pdf_path], "rejected")
                continue
            self._store(row, row_bytes, pdf_path, summary)
            self._archive(drop_dir, row_path.name, [row_path, pdf_path], "ingested")
        return summary

    @staticmethod
    def _pdf_is_valid(row: Manifest, pdf_path: Path) -> bool:
        if not pdf_path.is_file():
            return False
        body = pdf_path.read_bytes()
        return (
            body.startswith(b"%PDF")
            and hashlib.sha256(body).hexdigest() == row.doc_id
            and len(body) == row.size_bytes
        )

    def _store(
        self, row: Manifest, row_bytes: bytes, pdf_path: Path, summary: IngestSummary
    ) -> None:
        if row.status == "fetched":
            pdf_key = f"{PDF_PREFIX}{row.doc_id}.pdf"
            if not self._exists(pdf_key):
                self._put(pdf_key, pdf_path.read_bytes())
        row_key = f"{MANIFEST_PREFIX}{row.doc_id}.json"
        if self._exists(row_key):
            summary.duplicates += 1
            return
        self._put(row_key, row_bytes)
        if row.status == "fetched":
            summary.uploaded += 1
        else:
            summary.gaps += 1


def main(argv: list[str] | None = None) -> int:
    import boto3
    from botocore.client import Config

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--drop", default="data/drop", type=Path)
    parser.add_argument("--bucket", default="raw")
    args = parser.parse_args(argv)
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    summary = RawIngestor(s3, bucket=args.bucket).ingest_dir(args.drop)
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
