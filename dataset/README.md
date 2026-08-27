# Dataset

Version `0.1.0` is a deliberately small golden-set seed: one 10-K and one
10-Q from each of ten SEC registrants. The companies span ten business areas,
but this release is a regression fixture rather than a statistically
representative benchmark.

`manifest.jsonl` is the authoritative inventory. Every line identifies an
immutable SEC accession and its primary Inline XBRL HTML document. The
`sector` field describes sample diversity; it is not intended as an official
industry classification.

## Stages

- `raw/` is a local cache populated by the downloader and excluded from Git.
- `normalized/` will contain reviewed normalized references.
- `extractions/` will contain reviewed references grouped by extraction task.

The latter two directories are intentionally empty until their schemas and
annotation protocol are defined. Outputs from systems under test must not be
placed in these directories.

## Integrity

The first successful download creates `raw.lock.jsonl` beside the manifest.
It records the SHA-256 digest, size, and retrieval time of each artifact. The
raw files remain untracked, while the lock file should be reviewed and
committed as part of a dataset release. Later downloads are rejected if SEC
content does not match the committed lock.

Only the primary filing document is selected in this release. Exhibits and the
complete SEC submission package are explicitly out of scope.

SEC asks automated clients to identify themselves and limit request rates; see
its [developer guidance](https://www.sec.gov/about/developer-resources) and
[declared user-agent example](https://www.sec.gov/about/webmaster-frequently-asked-questions#developers).
Set `SEC_USER_AGENT` to an application name and contact email before running
the downloader. Do not commit personal contact details to this repository.
