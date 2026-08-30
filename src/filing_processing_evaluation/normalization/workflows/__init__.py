"""Explicit filing-type normalization workflows."""

from filing_processing_evaluation.normalization.workflows.ten_k import TenKNormalizer
from filing_processing_evaluation.normalization.workflows.ten_q import TenQNormalizer

__all__ = ["TenKNormalizer", "TenQNormalizer"]
