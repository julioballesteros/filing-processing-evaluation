"""Errors raised by the storage-independent normalization core."""

from __future__ import annotations


class NormalizationError(Exception):
    """A normalization failure attributed to a workflow stage."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        filing_id: str | None = None,
        source_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.filing_id = filing_id
        self.source_path = source_path

    def __str__(self) -> str:
        context = []
        if self.stage:
            context.append(f"stage={self.stage}")
        if self.source_path:
            context.append(f"source={self.source_path}")
        suffix = f" ({', '.join(context)})" if context else ""
        return self.message + suffix
