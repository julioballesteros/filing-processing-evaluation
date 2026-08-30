"""Explicit normalization workflow for SEC Form 10-Q filings."""

from __future__ import annotations

import hashlib

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    NormalizationInput,
    NormalizationResult,
)
from filing_processing_evaluation.normalization.workflows.components import (
    WorkflowStages,
)
from filing_processing_evaluation.normalization.workflows.sections import (
    TenQSectionPolicy,
)


class TenQNormalizer:
    """Normalize a loaded quarterly report through the 10-Q workflow."""

    form_type = "10-Q"

    def __init__(self, stages: WorkflowStages | None = None) -> None:
        self._stages = stages if stages is not None else WorkflowStages()
        self._section_policy = TenQSectionPolicy()

    def normalize(self, source: NormalizationInput) -> NormalizationResult:
        if source.metadata.form_type != self.form_type:
            raise NormalizationError(
                f"10-Q workflow cannot normalize {source.metadata.form_type}",
                stage="select_workflow",
                filing_id=source.metadata.filing_id,
            )
        diagnostics: list[Diagnostic] = []

        parsed = self._stages.parser.parse(source)
        diagnostics.extend(parsed.diagnostics)

        projected = self._stages.projector.project(parsed.value)
        diagnostics.extend(projected.diagnostics)

        classified = self._stages.classifier.classify(projected.value)
        diagnostics.extend(classified.diagnostics)

        structured = self._stages.block_builder.build(
            classified.value,
            section_policy=self._section_policy,
            filing_id=source.metadata.filing_id,
        )
        diagnostics.extend(structured.diagnostics)

        assembled = self._stages.assembler.assemble(
            source,
            structured.value,
            raw_sha256=hashlib.sha256(source.content).hexdigest(),
        )
        diagnostics.extend(assembled.diagnostics)
        return NormalizationResult(assembled.value, tuple(diagnostics))
