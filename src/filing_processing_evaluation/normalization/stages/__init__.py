"""Reusable stages composed by filing-specific normalization workflows."""

from filing_processing_evaluation.normalization.stages.assembly import (
    DocumentAssembler,
)
from filing_processing_evaluation.normalization.stages.blocks import (
    BlockBuilder,
    SectionPolicy,
)
from filing_processing_evaluation.normalization.stages.classification import (
    ElementClassifier,
)
from filing_processing_evaluation.normalization.stages.parsing import XhtmlParser
from filing_processing_evaluation.normalization.stages.projection import (
    InlineXbrlProjector,
)

__all__ = [
    "BlockBuilder",
    "DocumentAssembler",
    "ElementClassifier",
    "InlineXbrlProjector",
    "SectionPolicy",
    "XhtmlParser",
]
