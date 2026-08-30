"""Domain models shared across dataset and processing boundaries."""

from enum import StrEnum


class FormType(StrEnum):
    """Filing forms supported by the benchmark and normalization workflows."""

    TEN_K = "10-K"
    TEN_Q = "10-Q"
