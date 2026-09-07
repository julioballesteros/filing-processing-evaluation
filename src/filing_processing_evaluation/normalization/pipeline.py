"""Reusable composition of the stages that normalize one HTML artifact."""

from __future__ import annotations

from typing import Protocol

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    DocumentInput,
    NormalizationInput,
    NormalizationResult,
    ParsedXhtml,
    StageOutcome,
)
from filing_processing_evaluation.normalization.stages.assembly import (
    DocumentAssembler,
)
from filing_processing_evaluation.normalization.stages.blocks import BlockBuilder
from filing_processing_evaluation.normalization.stages.classification import (
    ElementClassifier,
)
from filing_processing_evaluation.normalization.stages.projection import (
    InlineXbrlProjector,
)
from filing_processing_evaluation.normalization.stages.sections import SectionPolicy


class DocumentParser(Protocol):
    """Parse one selected raw document into the shared HTML representation."""

    def parse(self, source: DocumentInput) -> StageOutcome[ParsedXhtml]: ...


class HtmlDocumentPipeline:
    """Apply reusable semantic stages to one artifact selected by a workflow."""

    def __init__(
        self, *, parser: DocumentParser, section_policy: SectionPolicy
    ) -> None:
        self._parser = parser
        self._section_policy = section_policy
        self._projector = InlineXbrlProjector()
        self._classifier = ElementClassifier()
        self._block_builder = BlockBuilder()
        self._assembler = DocumentAssembler()

    def normalize(
        self,
        source: NormalizationInput,
        *,
        artifact_id: str,
    ) -> NormalizationResult:
        """Normalize one named artifact from a fully loaded filing bundle."""
        try:
            artifact = source.artifacts[artifact_id]
        except KeyError as error:
            raise NormalizationError(
                f"filing bundle has no {artifact_id} artifact",
                stage="select_artifact",
                filing_id=source.metadata.filing_id,
            ) from error
        document_source = DocumentInput(metadata=source.metadata, artifact=artifact)
        diagnostics: list[Diagnostic] = []

        parsed = self._parser.parse(document_source)
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

        assembled = self._assembler.assemble(document_source, structured.value)
        diagnostics.extend(assembled.diagnostics)
        return NormalizationResult(assembled.value, tuple(diagnostics))
