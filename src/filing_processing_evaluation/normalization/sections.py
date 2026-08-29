"""SEC section-heading recognition."""

from __future__ import annotations

import re
from dataclasses import dataclass

PART_PATTERN = re.compile(r"^PART\s+([IVX]+)(?:\s*[—-]\s*(.*))?$", re.IGNORECASE)
ITEM_PATTERN = re.compile(r"^Item\s+(\d+[A-Z]?)\.?\s*(.*)$", re.IGNORECASE)
NOTE_PATTERN = re.compile("^Note\\s+\\d+[A-Z]?\\s*[\\u2013\\u2014-]", re.IGNORECASE)


@dataclass(frozen=True)
class SectionDefinition:
    """A recognized SEC section heading."""

    level: int
    label: str
    title: str | None


def section_definition(text: str) -> SectionDefinition | None:
    """Recognize structural SEC part, item, and signature headings."""
    if match := PART_PATTERN.fullmatch(text):
        label = f"PART {match.group(1).upper()}"
        title = match.group(2).strip(" .") if match.group(2) else None
        return SectionDefinition(level=1, label=label, title=title)
    if match := ITEM_PATTERN.match(text):
        label = f"Item {match.group(1).upper()}"
        title = match.group(2).strip(" .") or None
        return SectionDefinition(level=2, label=label, title=title)
    if text.upper() == "SIGNATURE":
        return SectionDefinition(level=1, label="SIGNATURE", title=None)
    return None
