"""CI and Dependabot configuration cover the supply-chain requirements."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI_TEXT = (ROOT / ".github/workflows/ci.yml").read_text()
CI = yaml.safe_load(CI_TEXT)
DEPENDABOT = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())


def test_ci_has_expected_jobs():
    assert {"lint", "test", "pip-audit", "npm-audit", "trivy", "digest-pins"} <= set(CI["jobs"])


def test_ci_commands_present():
    for needle in (
        "make lint",
        "make test",
        "pip-audit",
        "npm audit",
        "scripts/check_image_digests.py",
        "trivy",
    ):
        assert needle in CI_TEXT, needle


def test_npm_audit_and_trivy_are_guarded():
    assert "frontend/package.json" in CI_TEXT
    assert "Dockerfile" in CI_TEXT


def test_actions_are_sha_pinned():
    import re

    uses = re.findall(r"uses:\s*(\S+)", CI_TEXT)
    assert uses
    for u in uses:
        assert re.search(r"@[0-9a-f]{40}\b", u), f"action not SHA-pinned: {u}"


def test_dependabot_ecosystems():
    ecosystems = {u["package-ecosystem"] for u in DEPENDABOT["updates"]}
    assert {"pip", "npm", "docker", "github-actions"} <= ecosystems
    assert DEPENDABOT["version"] == 2


def test_contributing_documents_tests_first():
    text = (ROOT / "CONTRIBUTING.md").read_text().lower()
    assert "tests before implementation" in text
    assert "--no-verify" in text
