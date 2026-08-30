"""Shared deterministic fixtures and builders for the test suite."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from filing_processing_evaluation.application import FilingNormalizationApplication
from filing_processing_evaluation.artifacts import FileSystemRawFilingLoader
from filing_processing_evaluation.dataset import Filing, load_manifest
from filing_processing_evaluation.normalization import NormalizedDocument

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "dataset" / "manifest.jsonl"
SAMPLE_XHTML = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en-US">
  <head><title>Example Filing</title></head>
  <body>
    <div style="display:none"><span>hidden XBRL metadata</span></div>
    <table><tr><td></td></tr></table>
    <div><span style="font-weight:700">PART I - FINANCIAL INFORMATION</span></div>
    <div style="-sec-extract:summary"><span style="font-weight:700">Item 1. Statements</span></div>
    <div><span>Text&#160;with   <em>normalized</em> whitespace &amp; <span>facts</span>.</span></div>
    <div><span>(2)</span><span>In May</span> <span>Greater</span><span>China</span></div>
    <div><span style="font-style:italic;font-weight:400">Subheading</span></div>
    <div style="text-align:center"><span style="font-weight:700">(408) 996-1010</span></div>
    <ul><li>First item</li></ul>
    <table>
      <caption>Summary &amp; values</caption>
      <tr><th rowspan="2">Label</th><td>2026</td></tr>
      <tr><td colspan="0">42</td></tr>
    </table>
    <div><span>Example Inc. | Q1 2026 Form 10-Q | 1</span></div>
    <hr/>
  </body>
</html>
"""


def write_manifest(path: Path, entries: list[Filing]) -> None:
    """Write manifest entries in the dataset's deterministic JSONL format."""
    path.write_text(
        "".join(json.dumps(asdict(entry), sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


def prepare_normalization_fixture(tmp_path: Path) -> tuple[Filing, str, Path]:
    """Create one locked raw filing and return its manifest information."""
    entry = load_manifest(MANIFEST)[0]
    raw_path = tmp_path / entry.raw_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(SAMPLE_XHTML)
    digest = hashlib.sha256(SAMPLE_XHTML).hexdigest()
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [entry])
    lock = {
        "filing_id": entry.filing_id,
        "retrieved_at": "2026-08-27T10:00:00Z",
        "sha256": digest,
        "size_bytes": len(SAMPLE_XHTML),
    }
    (tmp_path / "raw.lock.jsonl").write_text(json.dumps(lock) + "\n")
    return entry, digest, manifest


def prepare_batch_normalization_fixture(
    tmp_path: Path,
) -> tuple[list[Filing], Path]:
    """Create one locked 10-K and 10-Q for batch and dispatch tests."""
    entries = load_manifest(MANIFEST)[:2]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, entries)
    locks = []
    for entry in entries:
        raw_path = tmp_path / entry.raw_path
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(SAMPLE_XHTML)
        locks.append(
            {
                "filing_id": entry.filing_id,
                "retrieved_at": "2026-08-27T10:00:00Z",
                "sha256": hashlib.sha256(SAMPLE_XHTML).hexdigest(),
                "size_bytes": len(SAMPLE_XHTML),
            }
        )
    (tmp_path / "raw.lock.jsonl").write_text(
        "".join(json.dumps(lock) + "\n" for lock in locks), encoding="utf-8"
    )
    return entries, manifest


def normalize_document(
    filing: Filing, *, dataset_dir: Path, expected_sha256: str
) -> NormalizedDocument:
    """Normalize one fixture filing through the application boundary."""
    application = FilingNormalizationApplication(FileSystemRawFilingLoader(dataset_dir))
    return application.normalize(filing, expected_sha256=expected_sha256).document
