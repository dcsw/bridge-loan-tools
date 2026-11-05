"""API settings, read from the environment (no defaults for secrets)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.api.lockdown import S3Credentials, is_remote

MIN_SECRET_LENGTH = 16
DEFAULT_AUDIT_RETENTION_DAYS = 365


@dataclass(frozen=True)
class RawStoreConfig:
    """MinIO/S3 raw-PDF bucket (source-document route)."""

    endpoint: str  # full URL, e.g. http://minio:9000
    bucket: str
    key_id: str
    secret: str
    region: str = "us-east-1"
    key_template: str = "{doc_id}.pdf"
    session_token: str | None = None


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    ``audit_retention_days`` (env ``AUDIT_RETENTION_DAYS``, default 365): audit entries older
    than this are purged at startup and at most once per 24 h while writing.
    """

    gateway_secret: str
    snapshot_base: str  # directory (local) or s3:// prefix holding manifest.json
    list_s3: S3Credentials | None = None  # credentials for the PII-free list path
    detail_s3: S3Credentials | None = None  # separate credentials for the borrower detail path
    raw_store: RawStoreConfig | None = None
    manifest_ttl_seconds: float = 60.0
    reload_min_interval_seconds: float = 1.0
    audit_log_path: str = "/var/lib/bridge-loan/audit/audit.log"
    audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS
    retry_after_seconds: int = 5
    response_cap_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        if len(self.gateway_secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"gateway_secret must be at least {MIN_SECRET_LENGTH} characters")
        if self.audit_retention_days < 1:
            raise ValueError("audit retention must be at least 1 day")
        if not self.snapshot_base:
            raise ValueError("snapshot_base is required")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        base = env.get("SNAPSHOT_BASE", "")
        s3_endpoint = env.get("SNAPSHOT_S3_ENDPOINT", "")

        def s3(prefix: str) -> S3Credentials | None:
            key = env.get(f"{prefix}_S3_KEY_ID")
            if not key or not is_remote(base):
                return None
            return S3Credentials(
                key_id=key,
                secret=env[f"{prefix}_S3_SECRET"],
                endpoint=s3_endpoint,
                scope=base.rstrip("/") + "/",
                use_ssl=env.get("SNAPSHOT_S3_USE_SSL", "false").lower() == "true",
                url_style=env.get("SNAPSHOT_S3_URL_STYLE", "path"),
                region=env.get("SNAPSHOT_S3_REGION", "us-east-1"),
            )

        raw = None
        if env.get("RAW_S3_ENDPOINT"):
            raw = RawStoreConfig(
                endpoint=env["RAW_S3_ENDPOINT"],
                bucket=env["RAW_S3_BUCKET"],
                key_id=env["RAW_S3_KEY_ID"],
                secret=env["RAW_S3_SECRET"],
                region=env.get("RAW_S3_REGION", "us-east-1"),
                key_template=env.get("RAW_KEY_TEMPLATE", "{doc_id}.pdf"),
            )
        try:
            retention = int(env.get("AUDIT_RETENTION_DAYS", DEFAULT_AUDIT_RETENTION_DAYS))
        except ValueError as exc:
            raise ValueError("AUDIT_RETENTION_DAYS must be an integer") from exc
        return cls(
            gateway_secret=env["GATEWAY_SECRET"],
            snapshot_base=base,
            list_s3=s3("LIST"),
            detail_s3=s3("DETAIL"),
            raw_store=raw,
            manifest_ttl_seconds=float(env.get("MANIFEST_TTL_SECONDS", 60)),
            audit_log_path=env.get("AUDIT_LOG_PATH", cls.audit_log_path),
            audit_retention_days=retention,
        )
