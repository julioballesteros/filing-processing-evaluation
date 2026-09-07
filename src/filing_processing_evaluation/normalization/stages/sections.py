"""Form-specific section recognition used by the block-building stage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

PART_PATTERN = re.compile(r"^PART\s+([IVX]+)(?:\s*[—-]\s*(.*))?$", re.IGNORECASE)
ITEM_PATTERN = re.compile(r"^Item\s+(\d+[A-Z]?)\.?\s*(.*)$", re.IGNORECASE)
NOTE_PATTERN = re.compile("^Note\\s+\\d+[A-Z]?\\s*[\\u2013\\u2014-]", re.IGNORECASE)
EARNINGS_RELEASE_SECTION_PATTERNS = (
    (
        re.compile(
            r"^(?:.*\s+)?(?:financial|business|operating|quarterly) highlights:?$",
            re.IGNORECASE,
        ),
        "HIGHLIGHTS",
    ),
    (
        re.compile(r"^(?:financial|quarterly|consolidated) results:?$", re.IGNORECASE),
        "RESULTS",
    ),
    (re.compile(r"^(?:outlook|guidance):?$", re.IGNORECASE), "OUTLOOK"),
    (
        re.compile(r"^(?:conference call|webcast)(?: information)?:?$", re.IGNORECASE),
        "CONFERENCE CALL",
    ),
    (
        re.compile(
            r"^(?:non-gaap|non-gaap financial measures)(?: reconciliations?)?:?$",
            re.IGNORECASE,
        ),
        "NON-GAAP",
    ),
)


@dataclass(frozen=True)
class SectionDefinition:
    """A recognized SEC section heading."""

    level: int
    label: str
    title: str | None


class SectionPolicy(Protocol):
    """Recognize form-specific top-level filing sections."""

    def definition(self, text: str) -> SectionDefinition | None: ...


def section_definition(text: str) -> SectionDefinition | None:
    """Recognize structural SEC part, item, and signature headings."""
    if match := PART_PATTERN.fullmatch(text):
        label = f"PART {match.group(1).upper()}"
        title = match.group(2).strip(" .") if match.group(2) else None
        return SectionDefinition(level=1, label=label, title=title)
    if match := ITEM_PATTERN.match(text):
        label = f"Item {match.group(1).upper()}"
        candidate_title = match.group(2).strip(" .")
        title = (
            candidate_title
            if candidate_title and not candidate_title.startswith(",")
            else None
        )
        return SectionDefinition(level=2, label=label, title=title)
    if text.upper() == "SIGNATURE":
        return SectionDefinition(level=1, label="SIGNATURE", title=None)
    return None


class TenKSectionPolicy:
    """Recognize the part and item hierarchy of an annual report."""

    def definition(self, text: str) -> SectionDefinition | None:
        return section_definition(text)


class TenQSectionPolicy:
    """Recognize the part and item hierarchy of a quarterly report."""

    def definition(self, text: str) -> SectionDefinition | None:
        return section_definition(text)


class EarningsReleaseSectionPolicy:
    """Recognize conservative top-level sections in an earnings announcement."""

    def definition(self, text: str) -> SectionDefinition | None:
        for pattern, label in EARNINGS_RELEASE_SECTION_PATTERNS:
            if pattern.fullmatch(text.strip()):
                return SectionDefinition(level=1, label=label, title=text.strip())
        return None
