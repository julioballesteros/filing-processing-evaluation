.PHONY: install hooks format format-check lint lint-fix typecheck test check dataset-validate dataset-download dataset-normalize dataset-render dataset-accept require-filing-id require-reviewer

FILING_ID ?=
FORM ?=
REVIEWER ?=

install:
	uv sync --all-groups

hooks:
	uv run pre-commit install

format:
	uv run ruff check --fix .
	uv run black .

format-check:
	uv run black --check .

lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

typecheck:
	uv run mypy

test:
	uv run pytest

check: format-check lint typecheck test

dataset-validate:
	uv run filing-processing-evaluation validate

dataset-download:
	uv run filing-processing-evaluation download

dataset-normalize:
	uv run filing-processing-evaluation normalize $(if $(strip $(FORM)),--form $(FORM)) $(if $(strip $(FILING_ID)),--filing-id $(FILING_ID))

dataset-render: require-filing-id
	uv run filing-processing-evaluation render --input dataset/normalized/$(FILING_ID).json --compare-raw

dataset-accept: require-filing-id require-reviewer
	uv run filing-processing-evaluation accept-normalized --filing-id $(FILING_ID) --reviewer "$(REVIEWER)"

require-filing-id:
	@test -n "$(strip $(FILING_ID))" || (echo "FILING_ID is required for this target" >&2; exit 2)

require-reviewer:
	@test -n "$(strip $(REVIEWER))" || (echo "REVIEWER is required for this target" >&2; exit 2)
