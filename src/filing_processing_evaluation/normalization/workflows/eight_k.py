"""Normalization workflow for SEC Form 8-K earnings announcements."""

from __future__ import annotations

from filing_processing_evaluation.models import FormType
from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    NormalizationInput,
    NormalizationResult,
)
from filing_processing_evaluation.normalization.pipeline import HtmlDocumentPipeline
from filing_processing_evaluation.normalization.stages.parsing import SecHtmlParser
from filing_processing_evaluation.normalization.stages.sections import (
    EarningsReleaseSectionPolicy,
)
from filing_processing_evaluation.normalization.stages.tables import (
    EarningsReleaseTablePolicy,
)


class EightKNormalizer:
    """Normalize the selected earnings-release exhibit from an 8-K filing bundle."""

    form_type = FormType.EIGHT_K

    def __init__(self) -> None:
        self._pipeline = HtmlDocumentPipeline(
            parser=SecHtmlParser(),
            section_policy=EarningsReleaseSectionPolicy(),
            table_policy=EarningsReleaseTablePolicy(),
        )

    def normalize(self, source: NormalizationInput) -> NormalizationResult:
        if source.metadata.form_type != self.form_type:
            raise NormalizationError(
                f"8-K workflow cannot normalize {source.metadata.form_type}",
                stage="select_workflow",
                filing_id=source.metadata.filing_id,
            )
        return self._pipeline.normalize(source, artifact_id="earnings-release")
