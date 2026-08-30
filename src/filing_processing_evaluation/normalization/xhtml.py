"""XHTML text and style interpretation helpers."""

from __future__ import annotations

import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass

XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
XML_LANGUAGE = "{http://www.w3.org/XML/1998/namespace}lang"
CONTAINER_TAGS = {"div", "p", "li", "ol", "ul", "table", "hr"}
TEXT_CONTAINER_TAGS = {"div", "p", "li"}


def local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def normalize_text(value: str) -> str:
    """Normalize Unicode and whitespace without changing content."""
    normalized = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    return " ".join(normalized.split())


def _needs_fragment_separator(left: str, right: str) -> bool:
    left_word = left.isalnum() or left in ")]%}\u201d\u2019"
    right_word = right.isalnum() or right in "([\u201c\u2018"
    return left_word and right_word


def _join_fragments(fragments: list[tuple[str, bool]]) -> str:
    combined = ""
    for fragment, continues_previous in fragments:
        if not fragment:
            continue
        if (
            combined
            and not continues_previous
            and not combined[-1].isspace()
            and not fragment[0].isspace()
            and _needs_fragment_separator(combined[-1], fragment[0])
        ):
            combined += " "
        combined += fragment
    return normalize_text(combined)


def _continues_fitted_span(left: ET.Element, right: ET.Element) -> bool:
    """Return whether adjacent generated spans form one uninterrupted text run."""
    if local_name(left.tag) != "span" or local_name(right.tag) != "span" or left.tail:
        return False
    left_style = _style_properties(left)
    return (
        left_style == _style_properties(right)
        and left_style.get("white-space") == "pre-wrap"
        and left_style.get("min-width") == "fit-content"
    )


def is_hidden(element: ET.Element) -> bool:
    """Return whether an element is hidden by an inline style."""
    style = element.attrib.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def visible_text(element: ET.Element) -> str:
    """Extract normalized text while excluding hidden descendants."""
    fragments: list[tuple[str, bool]] = []

    def collect(node: ET.Element, *, continues_previous: bool = False) -> bool:
        if is_hidden(node):
            return False
        emitted = False
        if node.text:
            fragments.append((node.text, continues_previous))
            emitted = True
        previous_child: ET.Element | None = None
        for child in node:
            if local_name(child.tag) == "br":
                fragments.append((" ", True))
                child_emitted = True
            else:
                child_emitted = collect(
                    child,
                    continues_previous=(
                        continues_previous
                        if not emitted
                        else previous_child is not None
                        and _continues_fitted_span(previous_child, child)
                    ),
                )
            emitted = emitted or child_emitted
            if child.tail:
                fragments.append((child.tail, False))
                emitted = True
            previous_child = child
        return emitted

    collect(element)
    return _join_fragments(fragments)


def has_descendant_container(element: ET.Element) -> bool:
    """Return whether an element contains another block container."""
    return any(
        local_name(descendant.tag) in CONTAINER_TAGS
        for descendant in list(element.iter())[1:]
    )


def _style_properties(element: ET.Element) -> dict[str, str]:
    properties: dict[str, str] = {}
    for declaration in element.attrib.get("style", "").split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        properties[name.strip().lower()] = value.strip().lower()
    return properties


def has_style(element: ET.Element, name: str, value: str) -> bool:
    """Return whether an element or descendant has an inline style value."""
    return any(
        _style_properties(descendant).get(name) == value
        for descendant in element.iter()
    )


@dataclass(frozen=True)
class StyleProfile:
    """Proportion of visible characters carrying inherited emphasis."""

    characters: int
    bold_characters: int
    italic_characters: int

    @property
    def mostly_bold(self) -> bool:
        return self.characters > 0 and self.bold_characters / self.characters >= 0.9

    @property
    def mostly_italic(self) -> bool:
        return self.characters > 0 and self.italic_characters / self.characters >= 0.9


def style_profile(element: ET.Element) -> StyleProfile:
    """Measure inherited bold and italic emphasis in visible text."""
    characters = 0
    bold_characters = 0
    italic_characters = 0

    def add_text(value: str | None, *, bold: bool, italic: bool) -> None:
        nonlocal characters, bold_characters, italic_characters
        count = sum(not character.isspace() for character in value or "")
        characters += count
        bold_characters += count if bold else 0
        italic_characters += count if italic else 0

    def collect(
        node: ET.Element, *, inherited_bold: bool, inherited_italic: bool
    ) -> None:
        if is_hidden(node):
            return
        properties = _style_properties(node)
        tag = local_name(node.tag)
        bold = inherited_bold or tag in {"b", "strong"}
        italic = inherited_italic or tag in {"em", "i"}
        if weight := properties.get("font-weight"):
            try:
                bold = int(weight) >= 600
            except ValueError:
                bold = weight in {"bold", "bolder"}
        if font_style := properties.get("font-style"):
            italic = font_style in {"italic", "oblique"}
        add_text(node.text, bold=bold, italic=italic)
        for child in node:
            collect(child, inherited_bold=bold, inherited_italic=italic)
            add_text(child.tail, bold=bold, italic=italic)

    collect(element, inherited_bold=False, inherited_italic=False)
    return StyleProfile(characters, bold_characters, italic_characters)
