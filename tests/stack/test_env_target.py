"""`make env` writes random, non-default credentials and never clobbers an existing file."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "MINIO_KMS_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "LAKEKEEPER_PG_ENCRYPTION_KEY",
]


def _make_env(path: Path):
    return subprocess.run(
        ["make", "env", f"ENV_FILE={path}"], cwd=ROOT, check=True, capture_output=True, text=True
    )


def _read(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def test_make_env_generates_random_values(tmp_path):
    a, b = tmp_path / "a.env", tmp_path / "b.env"
    _make_env(a)
    _make_env(b)
    va, vb = _read(a), _read(b)
    for key in REQUIRED:
        assert va[key] and vb[key], key
        assert va[key] != vb[key], f"{key} not random"
        assert "minioadmin" not in va[key].lower()
    assert len(va["MINIO_ROOT_PASSWORD"]) >= 24
    assert (a.stat().st_mode & 0o077) == 0, "env file must be private"


def test_make_env_does_not_overwrite(tmp_path):
    target = tmp_path / "x.env"
    _make_env(target)
    before = target.read_text()
    _make_env(target)
    assert target.read_text() == before


def test_env_is_gitignored():
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0
