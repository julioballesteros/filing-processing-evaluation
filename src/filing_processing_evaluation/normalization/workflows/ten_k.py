"""Explicit normalization workflow for SEC Form 10-K filings."""

from __future__ import annotations

import hashlib

from filing_processing_evaluation.models import FormType
from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    NormalizationInput,
    NormalizationResult,
)
from filing_processing_evaluation.normalization.stages.assembly import (
    DocumentAssembler,
)
from filing_processing_evaluation.normalization.stages.blocks import BlockBuilder
from filing_processing_evaluation.normalization.stages.classification import (
    ElementClassifier,
)
from filing_processing_evaluation.normalization.stages.parsing import XhtmlParser
from filing_processing_evaluation.normalization.stages.projection import (
    InlineXbrlProjector,
)
from filing_processing_evaluation.normalization.stages.sections import (
    TenKSectionPolicy,
)


class TenKNormalizer:
    """Normalize a loaded annual report through the 10-K workflow."""

    form_type = FormType.TEN_K

    def __init__(self) -> None:
        self._parser = XhtmlParser()
        self._projector = InlineXbrlProjector()
        self._classifier = ElementClassifier()
        self._block_builder = BlockBuilder()
        self._assembler = DocumentAssembler()
        self._section_policy = TenKSectionPolicy()

    def normalize(self, source: NormalizationInput) -> NormalizationResult:
        if source.metadata.form_type != self.form_type:
            raise NormalizationError(
                f"10-K workflow cannot normalize {source.metadata.form_type}",
                stage="select_workflow",
                filing_id=source.metadata.filing_id,
            )
        diagnostics: list[Diagnostic] = []

        parsed = self._parser.parse(source)
        diagnostics.extend(parsed.diagnostics)

        projected = self._projector.project(parsed.value)
        diagnostics.extend(projected.diagnostics)

        classified = self._classifier.classify(projected.value)
        diagnostics.extend(classified.diagnostics)

        structured = self._block_builder.build(
            classified.value,
            section_policy=self._section_policy,
            filing_id=source.metadata.filing_id,
        )
        diagnostics.extend(structured.diagnostics)

        assembled = self._assembler.assemble(
            source,
            structured.value,
            raw_sha256=hashlib.sha256(source.content).hexdigest(),
        )
        diagnostics.extend(assembled.diagnostics)
        return NormalizationResult(assembled.value, tuple(diagnostics))
