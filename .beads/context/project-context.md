# Project Context
## Tooling
- Python 3.12 via uv workspace (use `uv run --frozen`; `make sync` needs --all-packages), pytest, ruff (lint scope: tests scripts backend config), pytest-cov (`make cov`)
- Plan: docs/plans/2026-11-05-phase1-implementation-plan.md ; Design: docs/plans/2026-11-05-bridge-loan-platform-design.md
## Completed Work Units
| WU | Title | Commit |
| WU1 | Scaffold | 553e956 |
| WU2 | Models/classifier/fixtures/snapshot | see git log |
## Patterns
- classify_loan placeholder all-true (config/classifier.yaml); signals unevaluable not fired when data absent
- Known stray: .agents/skills/caveman-setup/SKILL.md whitespace edit - never stage
- TODO: digest scanner must skip .claude/ (WU3 merge); make sync --all-packages; WU7 verify log redaction end to end
