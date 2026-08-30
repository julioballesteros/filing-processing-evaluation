"""Build normalized blocks and sections from classified elements."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    ClassifiedDocument,
    ClassifiedTable,
    ClassifiedText,
    Diagnostic,
    SourceLocation,
    StageOutcome,
    StructuredDocument,
)
from filing_processing_evaluation.normalization.stages.sections import (
    NOTE_PATTERN,
    SectionDefinition,
    SectionPolicy,
)
from filing_processing_evaluation.normalization.stages.tables import normalize_table


class _StructureBuilder:
    def __init__(self, section_definition: Callable[[str], SectionDefinition | None]):
        self._section_definition = section_definition
        self.blocks: list[dict[str, Any]] = []
        self.sections: list[dict[str, Any]] = []
        self._section_by_level: dict[int, str] = {}
        self._saw_nonitalic_major_heading = False
        self._inside_note = False
        self._note_has_nonitalic_subheading = False
        self.discarded_empty_tables = 0

    @property
    def current_section_id(self) -> str | None:
        if not self._section_by_level:
            return None
        return self._section_by_level[max(self._section_by_level)]

    def add(self, value: ClassifiedText | ClassifiedTable) -> None:
        if isinstance(value, ClassifiedTable):
            self._add_table(value)
        else:
            self._add_text(value)

    def _next_block_id(self) -> str:
        return f"b{len(self.blocks) + 1:04d}"

    def _add_block(
        self,
        block_type: str,
        source: SourceLocation,
        **fields: Any,
    ) -> str:
        block_id = self._next_block_id()
        self.blocks.append(
            {
                "id": block_id,
                "order": len(self.blocks) + 1,
                "section_id": self.current_section_id,
                "type": block_type,
                "source": {
                    "page_number": source.page_number,
                    "path": source.path,
                },
                **fields,
            }
        )
        return block_id

    def _start_section(
        self, definition: SectionDefinition, heading_block_id: str
    ) -> str:
        parent_levels = [
            level for level in self._section_by_level if level < definition.level
        ]
        parent_id = (
            self._section_by_level[max(parent_levels)] if parent_levels else None
        )
        section_id = f"s{len(self.sections) + 1:04d}"
        self.sections.append(
            {
                "id": section_id,
                "order": len(self.sections) + 1,
                "parent_id": parent_id,
                "level": definition.level,
                "label": definition.label,
                "title": definition.title,
                "heading_block_id": heading_block_id,
            }
        )
        self._section_by_level = {
            level: existing_id
            for level, existing_id in self._section_by_level.items()
            if level < definition.level
        }
        self._section_by_level[definition.level] = section_id
        self._saw_nonitalic_major_heading = False
        self._inside_note = False
        self._note_has_nonitalic_subheading = False
        return section_id

    def _add_text(self, value: ClassifiedText) -> None:
        text = value.text
        features = value.features
        definition = self._section_definition(text)
        is_heading = definition is not None or (
            not features.is_phone
            and len(text) <= 300
            and (
                (
                    features.mostly_bold
                    and (features.centered or features.summary or text.isupper())
                )
                or (features.mostly_bold and features.word_count <= 45)
                or (
                    features.mostly_italic
                    and (
                        (features.mostly_bold and features.word_count <= 45)
                        or len(text) <= 120
                    )
                )
            )
        )
        if is_heading:
            if definition is not None:
                level = definition.level
            elif NOTE_PATTERN.match(text):
                level = 3
                self._inside_note = True
                self._note_has_nonitalic_subheading = False
            elif self._inside_note:
                level = (
                    5
                    if features.mostly_italic and self._note_has_nonitalic_subheading
                    else 4
                )
                if not features.mostly_italic:
                    self._note_has_nonitalic_subheading = True
            elif features.mostly_italic and not features.mostly_bold:
                level = 4 if self._saw_nonitalic_major_heading else 3
            else:
                level = 3
            block_id = self._add_block("heading", value.source, level=level, text=text)
            if definition is not None:
                section_id = self._start_section(definition, block_id)
                self.blocks[-1]["section_id"] = section_id
            elif not features.mostly_italic:
                self._saw_nonitalic_major_heading = True
            return
        block_type = "list_item" if value.tag == "li" else "paragraph"
        self._add_block(block_type, value.source, text=text)

    def _add_table(self, value: ClassifiedTable) -> None:
        normalized = normalize_table(value.element)
        if normalized is None:
            self.discarded_empty_tables += 1
            return
        self._add_block("table", value.source, **normalized)


class BlockBuilder:
    """Apply shared block rules and a form-specific section policy."""

    stage_name = "build_blocks"

    def build(
        self,
        classified: ClassifiedDocument,
        *,
        section_policy: SectionPolicy,
        filing_id: str,
    ) -> StageOutcome[StructuredDocument]:
        builder = _StructureBuilder(section_policy.definition)
        for value in classified.elements:
            builder.add(value)
        if not builder.blocks:
            raise NormalizationError(
                "normalization produced no blocks",
                stage=self.stage_name,
                filing_id=filing_id,
            )
        table_blocks = sum(block["type"] == "table" for block in builder.blocks)
        diagnostic = Diagnostic(
            stage=self.stage_name,
            code="blocks_built",
            message="Built normalized blocks and filing sections.",
            details={
                "blocks": len(builder.blocks),
                "sections": len(builder.sections),
                "tables": table_blocks,
                "discarded_empty_tables": builder.discarded_empty_tables,
            },
        )
        return StageOutcome(
            StructuredDocument(
                source_title=classified.source_title,
                language=classified.language,
                page_count=classified.page_count,
                sections=builder.sections,
                blocks=builder.blocks,
            ),
            (diagnostic,),
        )
