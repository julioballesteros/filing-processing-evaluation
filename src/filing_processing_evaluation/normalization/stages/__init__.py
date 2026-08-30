"""Reusable stages composed by filing-specific normalization workflows."""

from filing_processing_evaluation.normalization.stages.assembly import (
    DocumentAssembler,
)
from filing_processing_evaluation.normalization.stages.blocks import (
    BlockBuilder,
)
from filing_processing_evaluation.normalization.stages.classification import (
    ElementClassifier,
)
from filing_processing_evaluation.normalization.stages.parsing import XhtmlParser
from filing_processing_evaluation.normalization.stages.projection import (
    InlineXbrlProjector,
)
from filing_processing_evaluation.normalization.stages.sections import (
    SectionPolicy,
    TenKSectionPolicy,
    TenQSectionPolicy,
)

__all__ = [
    "BlockBuilder",
    "DocumentAssembler",
    "ElementClassifier",
    "InlineXbrlProjector",
    "SectionPolicy",
    "TenKSectionPolicy",
    "TenQSectionPolicy",
    "XhtmlParser",
]
