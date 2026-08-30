"""Shared stage dependencies composed explicitly by each workflow."""

from __future__ import annotations

from dataclasses import dataclass, field

from filing_processing_evaluation.normalization.stages import (
    BlockBuilder,
    DocumentAssembler,
    ElementClassifier,
    InlineXbrlProjector,
    XhtmlParser,
)


@dataclass(frozen=True)
class WorkflowStages:
    """Reusable stateless stages injected into filing-specific workflows."""

    parser: XhtmlParser = field(default_factory=XhtmlParser)
    projector: InlineXbrlProjector = field(default_factory=InlineXbrlProjector)
    classifier: ElementClassifier = field(default_factory=ElementClassifier)
    block_builder: BlockBuilder = field(default_factory=BlockBuilder)
    assembler: DocumentAssembler = field(default_factory=DocumentAssembler)
