# Contributing

## Tests before implementation

This project is test-driven. For every change:

1. Write the test first and watch it fail for the right reason (red).
2. Write the minimum code to make it pass (green).
3. Refactor with the tests still passing.

Commit the tests before the implementation so commit order shows tests before implementation;
reviewers verify this. Each work unit follows the same rule.

## Local checks

```bash
uv sync            # install dev dependencies
make lint          # ruff check + format check
make test          # pytest
make audit         # pip-audit
```

Install hooks once per clone with `pre-commit install`. Hooks run ruff and gitleaks (version
pinned in `.pre-commit-config.yaml` and CI).

## Rules

- Never commit with `--no-verify`; fix the hook failure instead.
- Never commit secrets. Copy `.env.example` to `.env` (git-ignored) and fill in real values.
- Docker `FROM` and compose `image:` references must be pinned with `@sha256:` digests
  (`scripts/check_image_digests.py`, enforced in CI).
- Do not suppress lint or type errors; fix the cause.
- Record architectural decisions as ADRs in `docs/adr/`.
