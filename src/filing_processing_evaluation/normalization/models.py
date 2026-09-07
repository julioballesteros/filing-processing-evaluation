"""Typed inputs, intermediate representations, and outputs for normalization."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from filing_processing_evaluation.models import FormType


@dataclass(frozen=True)
class FilingMetadata:
    """Metadata required by normalization, independent of artifact storage."""

    filing_id: str
    company_name: str
    form_type: FormType
    filing_date: str
    period_end_date: str
    event_date: str | None
    items: tuple[str, ...]


@dataclass(frozen=True)
class RawDocumentInput:
    """One verified raw artifact available to a normalization workflow."""

    artifact_id: str
    filename: str
    document_type: str
    sha256: str
    content: bytes


@dataclass(frozen=True)
class NormalizationInput:
    """A fully loaded filing bundle ready for deterministic processing."""

    metadata: FilingMetadata
    artifacts: Mapping[str, RawDocumentInput]


@dataclass(frozen=True)
class DocumentInput:
    """One artifact selected by a filing workflow for document processing."""

    metadata: FilingMetadata
    artifact: RawDocumentInput

    @property
    def content(self) -> bytes:
        """Return the selected artifact bytes."""
        return self.artifact.content


@dataclass(frozen=True)
class Diagnostic:
    """Deterministic information emitted by one normalization stage."""

    stage: str
    code: str
    message: str
    details: Mapping[str, int | str]


@dataclass(frozen=True)
class StageOutcome[T]:
    """A stage value together with diagnostics produced while creating it."""

    value: T
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class SourceLocation:
    """Stable location of an element in the submitted XHTML."""

    page_number: int
    path: str


@dataclass(frozen=True)
class ParsedXhtml:
    """Parsed XHTML metadata and body tree."""

    body: ET.Element
    source_title: str
    language: str


@dataclass(frozen=True)
class ProjectedText:
    """A visible leaf text container projected from the source tree."""

    element: ET.Element
    tag: str
    source: SourceLocation


@dataclass(frozen=True)
class ProjectedTable:
    """A visible source table awaiting logical-grid normalization."""

    element: ET.Element
    source: SourceLocation


type ProjectedElement = ProjectedText | ProjectedTable


@dataclass(frozen=True)
class ProjectedDocument:
    """Ordered visible HTML elements without hidden Inline XBRL metadata."""

    source_title: str
    language: str
    page_count: int
    elements: tuple[ProjectedElement, ...]


@dataclass(frozen=True)
class TextFeatures:
    """Layout and emphasis signals used for semantic text classification."""

    mostly_bold: bool
    mostly_italic: bool
    centered: bool
    summary: bool
    word_count: int
    is_phone: bool


@dataclass(frozen=True)
class ClassifiedText:
    """Normalized text plus classification signals and provenance."""

    text: str
    tag: str
    source: SourceLocation
    features: TextFeatures


@dataclass(frozen=True)
class ClassifiedTable:
    """A table distinguished from surrounding layout elements."""

    element: ET.Element
    source: SourceLocation


type ClassifiedElement = ClassifiedText | ClassifiedTable


@dataclass(frozen=True)
class ClassifiedDocument:
    """Ordered semantic candidates ready to become normalized blocks."""

    source_title: str
    language: str
    page_count: int
    elements: tuple[ClassifiedElement, ...]


@dataclass(frozen=True)
class StructuredDocument:
    """Normalized block and section structure before document assembly."""

    source_title: str
    language: str
    page_count: int
    sections: list[dict[str, Any]]
    blocks: list[dict[str, Any]]


class NormalizedMetadata(TypedDict):
    """Serializable metadata embedded in a normalized document."""

    title: str
    source_title: str
    language: str
    company_name: str
    form_type: FormType
    filing_date: str
    period_end_date: str
    event_date: str | None
    items: list[str]


class SourceArtifactMetadata(TypedDict):
    """Serializable identity and integrity metadata for normalized source content."""

    artifact_id: str
    filename: str
    document_type: str
    sha256: str


class NormalizedDocument(TypedDict):
    """Serializable normalized-document schema."""

    schema_version: str
    filing_id: str
    source_artifact: SourceArtifactMetadata
    document: NormalizedMetadata
    page_count: int
    sections: list[dict[str, Any]]
    blocks: list[dict[str, Any]]


@dataclass(frozen=True)
class NormalizationResult:
    """Processed document and deterministic diagnostics from every stage."""

    document: NormalizedDocument
    diagnostics: tuple[Diagnostic, ...]
