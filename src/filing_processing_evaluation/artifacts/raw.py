"""Adapters that load and verify raw filing artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from filing_processing_evaluation.artifacts.errors import ArtifactError
from filing_processing_evaluation.artifacts.files import sha256_bytes
from filing_processing_evaluation.dataset import Filing
from filing_processing_evaluation.normalization.models import (
    FilingMetadata,
    NormalizationInput,
)


class RawFilingLoader(Protocol):
    """Load a manifest filing into the normalization input contract."""

    def load(self, filing: Filing, *, expected_sha256: str) -> NormalizationInput: ...


class FileSystemRawFilingLoader:
    """Load raw filings from a dataset directory and verify their lock digest."""

    def __init__(self, dataset_dir: Path) -> None:
        self._dataset_dir = dataset_dir

    def load(self, filing: Filing, *, expected_sha256: str) -> NormalizationInput:
        raw_path = self._dataset_dir / filing.raw_path
        if not raw_path.is_file():
            raise ArtifactError(f"raw artifact not found: {raw_path}")
        content = raw_path.read_bytes()
        if sha256_bytes(content) != expected_sha256:
            raise ArtifactError(f"raw artifact failed integrity check: {raw_path}")
        metadata = FilingMetadata(
            filing_id=filing.filing_id,
            company_name=filing.company_name,
            form_type=filing.form_type,
            filing_date=filing.filing_date,
            period_end_date=filing.period_end_date,
            primary_document=filing.primary_document,
        )
        return NormalizationInput(metadata=metadata, content=content)
