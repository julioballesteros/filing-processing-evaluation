"""Storage-independent normalization for SEC Inline XBRL documents."""

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    FilingMetadata,
    NormalizationInput,
    NormalizationResult,
    NormalizedDocument,
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
    TenKNormalizer,
    TenQNormalizer,
)
from filing_processing_evaluation.normalization.xhtml import normalize_text

__all__ = [
    "SCHEMA_VERSION",
    "Diagnostic",
    "FilingMetadata",
    "NormalizationError",
    "NormalizationInput",
    "NormalizationResult",
    "NormalizationService",
    "NormalizedDocument",
    "TenKNormalizer",
    "TenQNormalizer",
    "_normalize_table",
    "_validate_table",
    "normalize_filing",
    "normalize_text",
    "validate_normalized",
]
