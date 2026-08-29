"""Deterministic normalization for SEC Inline XBRL documents.

``NormalizationService`` is the unified application interface. The module-level
functions remain available for existing callers and the command-line interface.
"""

from filing_processing_evaluation.normalization.schema import SCHEMA_VERSION
from filing_processing_evaluation.normalization.service import (
    NormalizationService,
    normalize_filing,
)
from filing_processing_evaluation.normalization.storage import (
    load_normalized,
    update_normalized_manifest,
    write_normalized,
)
from filing_processing_evaluation.normalization.tables import (
    normalize_table as _normalize_table,
)
from filing_processing_evaluation.normalization.validation import (
    validate_normalized,
)
from filing_processing_evaluation.normalization.validation import (
    validate_table as _validate_table,
)
from filing_processing_evaluation.normalization.xhtml import normalize_text

__all__ = [
    "SCHEMA_VERSION",
    "NormalizationService",
    "_normalize_table",
    "_validate_table",
    "load_normalized",
    "normalize_filing",
    "normalize_text",
    "update_normalized_manifest",
    "validate_normalized",
    "write_normalized",
]
