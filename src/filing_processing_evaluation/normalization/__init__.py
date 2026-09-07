"""Storage-independent normalization for SEC filing documents."""

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    DocumentInput,
    FilingMetadata,
    NormalizationInput,
    NormalizationResult,
    NormalizedDocument,
    RawDocumentInput,
    SourceArtifactMetadata,
)
from filing_processing_evaluation.normalization.schema import SCHEMA_VERSION
from filing_processing_evaluation.normalization.service import (
    NormalizationService,
    normalize_filing,
)
from filing_processing_evaluation.normalization.stages.tables import (
    normalize_table as _normalize_table,
)
from filing_processing_evaluation.normalization.validation import (
    validate_normalized,
)
from filing_processing_evaluation.normalization.validation import (
    validate_table as _validate_table,
)
from filing_processing_evaluation.normalization.workflows import (
    EightKNormalizer,
    TenKNormalizer,
    TenQNormalizer,
)
from filing_processing_evaluation.normalization.xhtml import normalize_text

__all__ = [
    "SCHEMA_VERSION",
    "Diagnostic",
    "DocumentInput",
    "EightKNormalizer",
    "FilingMetadata",
    "NormalizationError",
    "NormalizationInput",
    "NormalizationResult",
    "NormalizationService",
    "NormalizedDocument",
    "RawDocumentInput",
    "SourceArtifactMetadata",
    "TenKNormalizer",
    "TenQNormalizer",
    "_normalize_table",
    "_validate_table",
    "normalize_filing",
    "normalize_text",
    "validate_normalized",
]
