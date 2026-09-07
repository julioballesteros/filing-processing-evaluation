"""Application orchestration for loading and normalizing one filing."""

from __future__ import annotations

from collections.abc import Mapping

from filing_processing_evaluation.artifacts.raw import RawFilingLoader
from filing_processing_evaluation.dataset import Filing
from filing_processing_evaluation.normalization.models import NormalizationResult
from filing_processing_evaluation.normalization.service import NormalizationService


class FilingNormalizationApplication:
    """Coordinate an artifact loader with the storage-independent normalizer."""

    def __init__(
        self,
        loader: RawFilingLoader,
        service: NormalizationService | None = None,
    ) -> None:
        self._loader = loader
        self._service = service if service is not None else NormalizationService()

    def normalize(
        self,
        filing: Filing,
        *,
        expected_sha256_by_artifact: Mapping[str, str],
    ) -> NormalizationResult:
        source = self._loader.load(
            filing,
            expected_sha256_by_artifact=expected_sha256_by_artifact,
        )
        return self._service.normalize(source)
