.PHONY: install hooks format format-check lint lint-fix typecheck test check dataset-validate dataset-download

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
