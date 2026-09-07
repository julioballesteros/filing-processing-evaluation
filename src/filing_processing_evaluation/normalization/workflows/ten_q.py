"""Explicit normalization workflow for SEC Form 10-Q filings."""

from __future__ import annotations

from filing_processing_evaluation.models import FormType
from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    NormalizationInput,
    NormalizationResult,
)
from filing_processing_evaluation.normalization.pipeline import HtmlDocumentPipeline
from filing_processing_evaluation.normalization.stages.parsing import XhtmlParser
from filing_processing_evaluation.normalization.stages.sections import (
    TenQSectionPolicy,
)


class TenQNormalizer:
    """Normalize a loaded quarterly report through the 10-Q workflow."""

    form_type = FormType.TEN_Q

    def __init__(self) -> None:
        self._pipeline = HtmlDocumentPipeline(
            parser=XhtmlParser(),
            section_policy=TenQSectionPolicy(),
        )

    def normalize(self, source: NormalizationInput) -> NormalizationResult:
        if source.metadata.form_type != self.form_type:
            raise NormalizationError(
                f"10-Q workflow cannot normalize {source.metadata.form_type}",
                stage="select_workflow",
                filing_id=source.metadata.filing_id,
            )
        return self._pipeline.normalize(source, artifact_id="primary")
