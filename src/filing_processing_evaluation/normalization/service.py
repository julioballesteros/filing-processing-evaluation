"""Storage-independent dispatch to filing-specific normalization workflows."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from filing_processing_evaluation.models import FormType
from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    NormalizationInput,
    NormalizationResult,
)
from filing_processing_evaluation.normalization.workflows import (
    EightKNormalizer,
    TenKNormalizer,
    TenQNormalizer,
)


class NormalizationWorkflow(Protocol):
    """One independently callable filing-type normalization workflow."""

    form_type: FormType

    def normalize(self, source: NormalizationInput) -> NormalizationResult: ...


class NormalizationService:
    """Dispatch a loaded filing to its explicitly registered workflow."""

    def __init__(
        self, workflows: Iterable[NormalizationWorkflow] | None = None
    ) -> None:
        selected = (
            tuple(workflows)
            if workflows is not None
            else (TenKNormalizer(), TenQNormalizer(), EightKNormalizer())
        )
        self._workflows = {workflow.form_type: workflow for workflow in selected}
        if len(self._workflows) != len(selected):
            raise ValueError("normalization workflow form types must be unique")

    def normalize(self, source: NormalizationInput) -> NormalizationResult:
        """Normalize one fully loaded filing without performing storage operations."""
        form_type = source.metadata.form_type
        try:
            workflow = self._workflows[form_type]
        except KeyError as error:
            raise NormalizationError(
                f"no normalization workflow is registered for {form_type}",
                stage="select_workflow",
                filing_id=source.metadata.filing_id,
            ) from error
        return workflow.normalize(source)


_DEFAULT_SERVICE = NormalizationService()


def normalize_filing(source: NormalizationInput) -> NormalizationResult:
    """Normalize a loaded filing through the default workflow registry."""
    return _DEFAULT_SERVICE.normalize(source)
