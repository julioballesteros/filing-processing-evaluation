"""Artifact-loading and persistence adapters outside the normalization core."""

from filing_processing_evaluation.artifacts.errors import ArtifactError
from filing_processing_evaluation.artifacts.normalized import (
    load_normalized,
    update_normalized_manifest,
    write_normalized,
)
from filing_processing_evaluation.artifacts.raw import (
    FileSystemRawFilingLoader,
    RawFilingLoader,
)

__all__ = [
    "ArtifactError",
    "FileSystemRawFilingLoader",
    "RawFilingLoader",
    "load_normalized",
    "update_normalized_manifest",
    "write_normalized",
]
