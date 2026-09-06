# Filing Processing Evaluation

Reproducible dataset and evaluation tooling for financial-filing parsers and
LLM extractors. The dataset defines a 30-filing golden set with 10 SEC 10-K
filings, 10 SEC 10-Q filings, and 10 SEC 8-K earnings announcements. Each 8-K
raw entry includes its primary filing document and one selected earnings-release
exhibit.

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

This validates the manifest without requiring the raw cache. Use `--form 10-K`,
`--form 10-Q`, or `--form 8-K` to select one subset.

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
creates `dataset/raw.lock.jsonl` containing content hashes. Locks identify both
the filing and the artifact within that filing. Raw documents are ignored by
Git; the lock file is intended to be committed with a dataset release.
Downloads can be filtered with `--form` or repeated `--filing-id` arguments.
Selecting an 8-K downloads both its primary document and its declared
earnings-release exhibit:

```bash
uv run filing-processing-evaluation download --form 8-K
uv run filing-processing-evaluation validate --form 8-K --check-raw
```

## Build normalized drafts

Create deterministic normalized drafts for the supported 10-K and 10-Q raw
filings in the manifest:

```bash
uv run filing-processing-evaluation normalize
```

8-K earnings-release normalization is not implemented yet; raw 8-K entries are
skipped by the default normalization command and explicitly rejected by
`--form 8-K`. Use `--form 10-K`, `--form 10-Q`, or repeatable `--filing-id`
arguments to target a supported subset. The equivalent Make target has the same
defaults:

```bash
make dataset-normalize
make dataset-normalize FORM=10-Q
make dataset-normalize FILING_ID=MANIFEST_FILING_ID
```

The normalizer core is storage-independent: it accepts a fully loaded filing and
returns a normalized document plus deterministic stage diagnostics. Filesystem
loading, lock-hash verification, JSON persistence, and review-manifest updates
are artifact adapters outside the service. Separate `TenKNormalizer` and
`TenQNormalizer` workflows each own a fixed composition of XHTML parsing,
visible HTML projection, element classification, block/table construction, and
document assembly stages. Shared stage implementations do not make a workflow
externally configurable; form-specific workflows can diverge as their rules evolve.

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
