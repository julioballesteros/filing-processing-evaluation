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

## Build normalized drafts

Create deterministic normalized drafts for every locked raw filing in the
manifest:

```bash
uv run filing-processing-evaluation normalize
```

Use `--form 10-K`, `--form 10-Q`, or repeatable `--filing-id` arguments to
target a subset. The equivalent Make target has the same defaults:

```bash
make dataset-normalize
make dataset-normalize FORM=10-Q
make dataset-normalize FILING_ID=MANIFEST_FILING_ID
```

The normalizer core is storage-independent: it accepts a fully loaded filing and
returns a normalized document plus deterministic stage diagnostics. Filesystem
loading, lock-hash verification, JSON persistence, and review-manifest updates
are artifact adapters outside the service. Separate `TenKNormalizer` and
`TenQNormalizer` workflows explicitly compose the shared XHTML parsing, visible
HTML projection, element classification, block/table construction, and document
assembly stages.

Add `--diagnostics` to inspect the counts and decisions emitted by every stage:

```bash
uv run filing-processing-evaluation normalize \
  --filing-id MANIFEST_FILING_ID \
  --diagnostics
```

Each successful filing is written immediately and recorded in the normalized
manifest. A malformed filing does not prevent the remaining selected filings
from being attempted, but the command returns a nonzero status if any filing
fails.

To inspect one result, set `FILING_ID` to an entry from `dataset/manifest.jsonl`
and render it beside the raw SEC document:

```bash
FILING_ID=MANIFEST_FILING_ID
uv run filing-processing-evaluation render \
  --input "dataset/normalized/${FILING_ID}.json" \
  --compare-raw
```

The comparison page is written to `runs/rendered/` and excluded from Git. The
generated JSON is a baseline draft, not automatically canonical: review block
boundaries, section assignments, and table topology before accepting it as
benchmark truth. This keeps the system under test independent from reference
creation.

Normalization produces logical tables rather than the SEC document's spacer
grid: empty layout cells are removed, currency and percentage fragments are
joined, column headers and row headers are identified, and original cell
coordinates remain available as provenance. Every block also records its
source page number and XHTML path. Page boundaries are audit metadata rather
than scored semantic blocks.

Default normalization updates `dataset/normalized/manifest.jsonl` with the
artifact hash and a `draft` review state. After completing the visual review,
record human acceptance explicitly:

```bash
uv run filing-processing-evaluation accept-normalized \
  --filing-id "${FILING_ID}" \
  --reviewer "Your Name"
```

The corresponding general Make workflows are `make dataset-render
FILING_ID=...` and `make dataset-accept FILING_ID=... REVIEWER="Your Name"`.
Both require an explicit filing ID; no company-specific default is assumed.

Acceptance is preserved when an identical artifact is regenerated and reset to
`draft` whenever its content hash changes.


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
