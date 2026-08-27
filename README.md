# Filing Processing Evaluation

Reproducible dataset and evaluation tooling for financial-filing parsers and
LLM extractors. The first release defines a 20-document golden-set seed with
10 SEC 10-K filings and 10 SEC 10-Q filings.

The benchmark is evaluation infrastructure, not training data. Raw filings
are reconstructed from immutable SEC accessions, reviewed references are
versioned, and candidate service outputs belong outside the dataset.

See [`dataset/README.md`](dataset/README.md) for the selection and stage
contract.

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)

## Set up development

```bash
uv sync
uv run pre-commit install
uv run pre-commit run --all-files
```

Commit the generated `uv.lock` file so local development and CI use the same
resolved dependencies.


## Validate the dataset

```bash
uv run filing-processing-evaluation validate
```

This validates the manifest without requiring the raw cache. Use
`--form 10-K` or `--form 10-Q` to select one subset.

## Construct the raw stage

[SEC fair-access guidance](https://www.sec.gov/about/developer-resources)
asks automated clients to identify themselves and moderate their request rate.
Set a user agent containing your application and contact email, then download:

```bash
export SEC_USER_AGENT="filing-processing-evaluation your-email@example.com"
uv run filing-processing-evaluation download
uv run filing-processing-evaluation validate --check-raw
```

The downloader is sequential, rate-limited, uses atomic file replacement, and
creates `dataset/raw.lock.jsonl` containing content hashes. Raw documents are
ignored by Git; the lock file is intended to be committed with a dataset
release. Downloads can be filtered with `--form` or repeated `--filing-id`
arguments.


## Quality checks

```bash
uv run black .
uv run ruff check .
uv run mypy
uv run pytest
```

The same commands are available through `make format`, `make lint`,
`make typecheck`, `make test`, and `make check`.


GitHub Actions runs the complete check suite, including offline manifest
validation, for pushes to `main` and pull requests. Tests never call SEC.


## Updating from the template

From a clean Git working tree:

```bash
uvx copier update
```

Review and test the resulting diff before committing it.
