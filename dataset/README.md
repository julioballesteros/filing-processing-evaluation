# Dataset

Version `0.3.0` is a deliberately small golden set: one 10-K, one 10-Q, and one
earnings-announcement 8-K from each of ten SEC registrants. The companies span
ten business areas, but this release is a regression fixture rather than a
statistically representative benchmark.

`manifest.jsonl` is the authoritative inventory. Every line identifies an
immutable SEC accession and its primary Inline XBRL HTML document. An 8-K entry
also declares one explicitly selected HTML earnings-release exhibit, the SEC
items used to select the filing, and the filing's event date. Exhibit selection
is explicit because an accession may contain several `EX-99.*` documents with
different purposes. The `sector` field describes sample diversity; it is not
intended as an official industry classification.

## Stages

- `raw/` is a local cache populated by the downloader and excluded from Git.
- `normalized/` contains references conforming to
  `schemas/normalized-document.schema.json`.
- `extractions/` will contain reviewed references grouped by extraction task.

Every normalized document follows the same provider-independent contract and
remains a generated draft until its block boundaries, heading hierarchy, and
logical tables have been reviewed. Normalized documents contain:

- A schema version, filing ID, and the selected source artifact's identity and
  SHA-256.
- Self-contained filing metadata and a human-readable document title.
- A flat, ordered list of headings, paragraphs, list items, and logical tables.
- A separate section hierarchy pointing to heading blocks.
- Logical table cells with inferred header roles and compact coordinates.
- Source page numbers and XHTML paths for every block, plus original table-cell
  coordinates for auditability.

`normalized/manifest.jsonl` records each reference path, schema version,
content hash, and review state. Generated artifacts enter as `draft`; only the
explicit `accept-normalized` workflow marks a human-reviewed artifact as
`reviewed`. A changed content hash resets that status.

The baseline excludes hidden Inline XBRL metadata, empty layout tables, spacer
rows and columns, recurring page furniture, and semantic page-break blocks. It
preserves visible values but does not yet normalize Inline XBRL facts into a
separate fact model. For an 8-K, the normalized content comes from the declared
`earnings-release` exhibit rather than concatenating it with the primary filing.
Presentation-only HTML tables are unfolded into ordered text while financial
data grids retain logical rows, columns, cell roles, and source coordinates.
Image-backed exhibits with flattened accessibility text remain page-level text
because the source HTML does not expose reliable table or heading boundaries.
`extractions/` remains empty until its annotation protocol is defined. Outputs
from systems under test must not be placed in either reference directory.

## Integrity

The first successful download creates `raw.lock.jsonl` beside the manifest.
It records the filing ID, filing-local artifact ID, SHA-256 digest, size, and
retrieval time of each artifact. The raw files remain untracked, while the lock
file should be reviewed and committed as part of a dataset release. Later
downloads are rejected if SEC content does not match the committed lock.

The raw 8-K subset includes each primary filing and its declared
`earnings-release` exhibit. Other exhibits, presentations, financial
supplements, graphics, and the complete SEC submission package remain out of
scope.

SEC asks automated clients to identify themselves and limit request rates; see
its [developer guidance](https://www.sec.gov/about/developer-resources) and
[declared user-agent example](https://www.sec.gov/about/webmaster-frequently-asked-questions#developers).
Set `SEC_USER_AGENT` to an application name and contact email before running
the downloader. Do not commit personal contact details to this repository.
