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
            r"^(?:(?:first|second|third|fourth)[ -]quarter\s+)?"
            r"(?:(?:financial|business|operating|quarterly)\s+)?highlights"
            r"(?:\s+and\s+strategic\s+initiatives|,\s*product releases,\s*"
            r"and customer stories)?(?:\s+dollars in\b.*)?$",
            re.IGNORECASE,
        ),
        "HIGHLIGHTS",
    ),
    (
        re.compile(
            r"^(?:q[1-4]\s+fiscal\s+\d{4}\s+summary|"
            r"fiscal\s+year\s+\d{4}\s+results|"
            r"(?:first|second|third|fourth)[ -]quarter\s+\d{4}\s+results|"
            r"(?:financial|quarterly|consolidated)\s+(?:results|performance)|"
            r"results\s+summary|key\s+financial\s+metrics(?:\s+dollars in\b.*)?|"
            r"operating review\s*[\u2013\u2014-]\s*"
            r"(?:three|six|nine|twelve)\s+months ended\b.*)(?:\s+\d+)?$",
            re.IGNORECASE,
        ),
        "RESULTS",
    ),
    (
        re.compile(
            r"^(?:(?:(?:business|financial)\s+)?(?:outlook|guidance)|"
            r"(?:first|second|third|fourth)\s+quarter\s+\d{4}\s+considerations)$",
            re.IGNORECASE,
        ),
        "OUTLOOK",
    ),
    (
        re.compile(
            r"^(?:conference call(?: and webcast)?|webcast)"
            r"(?: information| details)?$",
            re.IGNORECASE,
        ),
        "CONFERENCE CALL",
    ),
    (
        re.compile(
            r"^(?:(?:frequently used terms and )?non-gaap(?: financial)? "
            r"(?:measures|definition)(?: reconciliations?)?|"
            r"(?:\d+\.\s*)?notes on non-gaap financial measures|"
            r"reconciliations? of(?: and other information regarding)? "
            r"non-gaap financial measures.*|"
            r"reconciliation of gaap (?:and|to) non-gaap.*)$",
            re.IGNORECASE,
        ),
        "NON-GAAP",
    ),
    (
        re.compile(
            r"^(?:.+?\s+)?(?:condensed\s+)?consolidated\s+"
            r"(?:statements? of (?:operations|income|cash flows|comprehensive income)|"
            r"balance sheets?)(?:\s+\(unaudited\))?$",
            re.IGNORECASE,
        ),
        "FINANCIAL STATEMENTS",
    ),
    (
        re.compile(
            r"^(?:forward-looking statements|cautionary statement)$",
            re.IGNORECASE,
        ),
        "FORWARD-LOOKING",
    ),
    (re.compile(r"^about\s+.+$", re.IGNORECASE), "ABOUT"),
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
        title = text.strip().rstrip(":").strip()
        if len(title) > 240 or len(title.split()) > 30:
            return None
        candidate = re.sub(r"\bhi\s+ghlights\b", "highlights", title, flags=re.I)
        for pattern, label in EARNINGS_RELEASE_SECTION_PATTERNS:
            if pattern.fullmatch(candidate):
                return SectionDefinition(level=1, label=label, title=title)
        return None
