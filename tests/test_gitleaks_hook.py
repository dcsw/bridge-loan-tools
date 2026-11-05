"""Prove the pinned gitleaks pre-commit hook blocks a staged secret."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".pre-commit-config.yaml"
# Fake AWS-style key assembled at runtime so this source file itself contains no
# key-shaped literal (gitleaks would flag it); matches the aws-access-token rule.
FAKE_SECRET = 'aws_access_key_id = "' + "AKIA" + "IOSFODNN7ABCDEFG" + '"\n'


def _gitleaks_repo():
    config = yaml.safe_load(CONFIG.read_text())
    return next(r for r in config["repos"] if "gitleaks" in r["repo"])


def test_gitleaks_version_is_pinned_to_a_release_tag():
    assert re.fullmatch(r"v\d+\.\d+\.\d+", _gitleaks_repo()["rev"])


def test_gitleaks_hook_declared():
    assert [h["id"] for h in _gitleaks_repo()["hooks"]] == ["gitleaks"]


def test_ci_pins_same_gitleaks_version():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert _gitleaks_repo()["rev"] in ci


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


def test_hook_blocks_staged_secret(tmp_path):
    if shutil.which("gitleaks") is None:
        pytest.skip(
            "gitleaks binary not installed; install the pinned version to run "
            "this test (CI installs it)"
        )
    if shutil.which("pre-commit") is None:
        pytest.skip("pre-commit not installed")
    if shutil.which("go") is None:
        pytest.skip("go toolchain missing; pre-commit cannot build the gitleaks hook")

    shutil.copy(CONFIG, tmp_path / ".pre-commit-config.yaml")
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    assert _git(tmp_path, "init", "-q", env=env).returncode == 0
    install = subprocess.run(
        ["pre-commit", "install"], cwd=tmp_path, capture_output=True, text=True, env=env
    )
    assert install.returncode == 0, install.stderr
    (tmp_path / "config.txt").write_text(FAKE_SECRET)
    _git(tmp_path, "add", "config.txt", ".pre-commit-config.yaml", env=env)
    result = _git(tmp_path, "commit", "-q", "-m", "add secret", env=env)
    assert result.returncode != 0, "commit with a planted secret was not blocked"
    assert "leaks found" in (result.stdout + result.stderr).lower()
    assert _git(tmp_path, "rev-parse", "HEAD", env=env).returncode != 0
