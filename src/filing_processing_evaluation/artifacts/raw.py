"""Adapters that load and verify raw filing artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from filing_processing_evaluation.artifacts.errors import ArtifactError
from filing_processing_evaluation.artifacts.files import sha256_bytes
from filing_processing_evaluation.dataset import Filing
from filing_processing_evaluation.normalization.models import (
    FilingMetadata,
    NormalizationInput,
    RawDocumentInput,
)


class RawFilingLoader(Protocol):
    """Load a manifest filing into the normalization input contract."""

    def load(
        self,
        filing: Filing,
        *,
        expected_sha256_by_artifact: Mapping[str, str],
    ) -> NormalizationInput: ...


class FileSystemRawFilingLoader:
    """Load raw filings from a dataset directory and verify their lock digest."""

    def __init__(self, dataset_dir: Path) -> None:
        self._dataset_dir = dataset_dir

    def load(
        self,
        filing: Filing,
        *,
        expected_sha256_by_artifact: Mapping[str, str],
    ) -> NormalizationInput:
        declared = {artifact.artifact_id: artifact for artifact in filing.raw_artifacts}
        expected_ids = set(expected_sha256_by_artifact)
        declared_ids = set(declared)
        if expected_ids != declared_ids:
            missing = sorted(declared_ids - expected_ids)
            unknown = sorted(expected_ids - declared_ids)
            raise ArtifactError(
                f"raw artifact hashes do not match {filing.filing_id}; "
                f"missing={missing}, unknown={unknown}"
            )
        loaded: dict[str, RawDocumentInput] = {}
        for artifact_id, artifact in declared.items():
            raw_path = self._dataset_dir / artifact.raw_path
            if not raw_path.is_file():
                raise ArtifactError(f"raw artifact not found: {raw_path}")
            content = raw_path.read_bytes()
            expected_sha256 = expected_sha256_by_artifact[artifact_id]
            if sha256_bytes(content) != expected_sha256:
                raise ArtifactError(f"raw artifact failed integrity check: {raw_path}")
            loaded[artifact_id] = RawDocumentInput(
                artifact_id=artifact_id,
                filename=artifact.filename,
                document_type=artifact.document_type,
                sha256=expected_sha256,
                content=content,
            )
        metadata = FilingMetadata(
            filing_id=filing.filing_id,
            company_name=filing.company_name,
            form_type=filing.form_type,
            filing_date=filing.filing_date,
            period_end_date=filing.period_end_date,
            event_date=filing.event_date,
            items=filing.items,
        )
        return NormalizationInput(metadata=metadata, artifacts=loaded)
