# WU2: shared models, normalizers, classifier, fixtures.
.PHONY: cov fixtures fixtures-5k

cov:  ## Coverage gates: >=80% backend/common, >=95% branch on classify + signals
	uv run --frozen pytest tests/common --cov=backend.common --cov-branch \
		--cov-report=term-missing --cov-fail-under=80
	uv run --frozen coverage report --include='backend/common/classify.py,backend/common/signals.py' \
		--fail-under=95

fixtures:  ## Regenerate synthetic fixtures (tests/fixtures)
	uv run --frozen python scripts/gen_fixtures.py

fixtures-5k:  ## Regenerate fixtures plus the 5k-row synthetic snapshot
	uv run --frozen python scripts/gen_fixtures.py --with-5k
