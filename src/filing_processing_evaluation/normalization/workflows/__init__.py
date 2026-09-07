"""Explicit filing-type normalization workflows."""

from filing_processing_evaluation.normalization.workflows.eight_k import (
    EightKNormalizer,
)
from filing_processing_evaluation.normalization.workflows.ten_k import TenKNormalizer
from filing_processing_evaluation.normalization.workflows.ten_q import TenQNormalizer

__all__ = ["EightKNormalizer", "TenKNormalizer", "TenQNormalizer"]
