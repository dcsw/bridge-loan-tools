"""`.env` and `data/` must never be committable."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "path",
    [".env", "data/", "data/drop/x.pdf", "raw/x.pdf", "frontend/node_modules/x"],
)
def test_path_is_ignored(path):
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, capture_output=True)
    assert result.returncode == 0, f"{path} is not git-ignored"


def test_env_example_is_not_ignored():
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"], cwd=ROOT, capture_output=True
    )
    assert result.returncode == 1


def test_env_example_has_only_placeholders():
    text = (ROOT / ".env.example").read_text()
    values = {
        k: v
        for k, _, v in (
            line.partition("=") for line in text.splitlines() if line and not line.startswith("#")
        )
    }
    for key in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "LAKEKEEPER_PG_PASSWORD",
        "GATEWAY_SHARED_SECRET",
        "JWT_SIGNING_KEY",
    ):
        assert key in values, key
        assert "change-me" in values[key], key
    assert "minioadmin" not in text
