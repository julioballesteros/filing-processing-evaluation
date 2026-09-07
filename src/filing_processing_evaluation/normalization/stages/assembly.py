"""Assemble and validate the final normalized document."""

from __future__ import annotations

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    DocumentInput,
    NormalizedDocument,
    NormalizedMetadata,
    SourceArtifactMetadata,
    StageOutcome,
    StructuredDocument,
)
from filing_processing_evaluation.normalization.schema import SCHEMA_VERSION
from filing_processing_evaluation.normalization.validation import validate_normalized


class DocumentAssembler:
    """Create the stable output schema from normalized structure."""

    stage_name = "assemble_document"

    def assemble(
        self,
        source: DocumentInput,
        structured: StructuredDocument,
    ) -> StageOutcome[NormalizedDocument]:
        metadata = source.metadata
        document_metadata = NormalizedMetadata(
            title=(
                f"{metadata.company_name} {metadata.form_type} for period ended "
                f"{metadata.period_end_date}"
            ),
            source_title=structured.source_title,
            language=structured.language,
            company_name=metadata.company_name,
            form_type=metadata.form_type,
            filing_date=metadata.filing_date,
            period_end_date=metadata.period_end_date,
            event_date=metadata.event_date,
            items=list(metadata.items),
        )
        artifact = source.artifact
        source_artifact = SourceArtifactMetadata(
            artifact_id=artifact.artifact_id,
            filename=artifact.filename,
            document_type=artifact.document_type,
            sha256=artifact.sha256,
        )
        document = NormalizedDocument(
            schema_version=SCHEMA_VERSION,
            filing_id=metadata.filing_id,
            source_artifact=source_artifact,
            document=document_metadata,
            page_count=structured.page_count,
            sections=structured.sections,
            blocks=structured.blocks,
        )
        try:
            validate_normalized(document)
        except NormalizationError as error:
            raise NormalizationError(
                error.message,
                stage=self.stage_name,
                filing_id=metadata.filing_id,
                source_path=error.source_path,
            ) from error
        diagnostic = Diagnostic(
            stage=self.stage_name,
            code="document_validated",
            message="Assembled and validated the normalized document.",
            details={
                "page_count": structured.page_count,
                "blocks": len(structured.blocks),
                "sections": len(structured.sections),
            },
        )
        return StageOutcome(document, (diagnostic,))
