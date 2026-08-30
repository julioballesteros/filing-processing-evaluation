"""Typer command-line application for the filing benchmark dataset."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Never

import typer

from filing_processing_evaluation.application import FilingNormalizationApplication
from filing_processing_evaluation.artifacts import (
    ArtifactError,
    FileSystemRawFilingLoader,
    load_normalized,
    update_normalized_manifest,
    write_normalized,
)
from filing_processing_evaluation.dataset import (
    DatasetError,
    Filing,
    download_filings,
    load_lock,
    load_manifest,
    validate_dataset,
)
from filing_processing_evaluation.normalization import (
    NormalizationError,
)
from filing_processing_evaluation.rendering import (
    relative_href,
    render_normalized_html,
    write_rendered_html,
)

DEFAULT_MANIFEST = Path("dataset/manifest.jsonl")

app = typer.Typer(
    name="filing-processing-evaluation",
    help="Build and validate the filing-processing benchmark dataset.",
    no_args_is_help=True,
)


class FormType(StrEnum):
    """Filing forms currently supported by the dataset."""

    annual = "10-K"
    quarterly = "10-Q"


def _form_value(form: FormType | None) -> str | None:
    return form.value if form is not None else None


def _abort(error: DatasetError | ArtifactError | NormalizationError) -> Never:
    typer.echo(f"error: {error}")
    raise typer.Exit(code=2)


def _find_filing(entries: list[Filing], filing_id: str) -> Filing:
    for entry in entries:
        if entry.filing_id == filing_id:
            return entry
    raise DatasetError(f"unknown filing ID: {filing_id}")


def _select_filings(
    entries: list[Filing], *, form_type: str | None, filing_ids: set[str]
) -> list[Filing]:
    known_ids = {entry.filing_id for entry in entries}
    unknown_ids = sorted(filing_ids - known_ids)
    if unknown_ids:
        raise DatasetError(f"unknown filing IDs: {', '.join(unknown_ids)}")
    selected = [
        entry
        for entry in entries
        if form_type in {None, entry.form_type}
        and (not filing_ids or entry.filing_id in filing_ids)
    ]
    if not selected:
        raise DatasetError("filing selection is empty")
    return selected


@app.command()
def validate(
    manifest: Annotated[
        Path,
        typer.Option(help="Path to the filing manifest."),
    ] = DEFAULT_MANIFEST,
    check_raw: Annotated[
        bool,
        typer.Option(
            help="Require every selected raw file and verify it against the lock file."
        ),
    ] = False,
    form: Annotated[
        FormType | None,
        typer.Option(help="Validate only one filing form."),
    ] = None,
) -> None:
    """Validate the manifest, lock file, and optional raw files."""
    try:
        summary = validate_dataset(
            manifest, check_raw=check_raw, form_type=_form_value(form)
        )
    except DatasetError as error:
        _abort(error)
    typer.echo(
        f"Valid dataset: {summary.total} filings "
        f"({summary.by_form['10-K']} 10-K, {summary.by_form['10-Q']} 10-Q)"
    )


@app.command()
def download(
    manifest: Annotated[
        Path,
        typer.Option(help="Path to the filing manifest."),
    ] = DEFAULT_MANIFEST,
    form: Annotated[
        FormType | None,
        typer.Option(help="Download only one filing form."),
    ] = None,
    filing_id: Annotated[
        list[str] | None,
        typer.Option(help="Download one filing ID; repeat to select multiple filings."),
    ] = None,
    user_agent: Annotated[
        str | None,
        typer.Option(
            envvar="SEC_USER_AGENT",
            help="SEC-compliant identity; defaults to SEC_USER_AGENT.",
        ),
    ] = None,
    delay: Annotated[
        float,
        typer.Option(help="Minimum delay between requests in seconds."),
    ] = 0.2,
    force: Annotated[
        bool,
        typer.Option(help="Download files that already exist again."),
    ] = False,
) -> None:
    """Download raw primary documents from SEC EDGAR."""
    try:
        if not user_agent:
            raise DatasetError(
                "SEC_USER_AGENT is required. Use a value identifying your application "
                "and contact email, as required by SEC fair-access guidance."
            )
        entries = load_manifest(manifest)
        downloaded = download_filings(
            entries,
            dataset_dir=manifest.parent,
            user_agent=user_agent,
            form_type=_form_value(form),
            filing_ids=set(filing_id or []),
            delay=delay,
            force=force,
        )
    except DatasetError as error:
        _abort(error)
    typer.echo(f"Raw dataset ready: {downloaded} filing(s)")


@app.command()
def normalize(
    manifest: Annotated[
        Path,
        typer.Option(help="Path to the filing manifest."),
    ] = DEFAULT_MANIFEST,
    form: Annotated[
        FormType | None,
        typer.Option(help="Normalize only one filing form."),
    ] = None,
    filing_id: Annotated[
        list[str] | None,
        typer.Option(
            help="Normalize one filing ID; repeat to select multiple filings."
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            help=(
                "Output JSON path when exactly one filing is selected; defaults to "
                "dataset/normalized/<filing-id>.json."
            )
        ),
    ] = None,
    show_diagnostics: Annotated[
        bool,
        typer.Option(
            "--diagnostics",
            help="Print deterministic diagnostics emitted by each workflow stage.",
        ),
    ] = False,
) -> None:
    """Create reviewable normalized drafts for selected locked raw filings."""
    try:
        entries = load_manifest(manifest)
        filings = _select_filings(
            entries,
            form_type=_form_value(form),
            filing_ids=set(filing_id or []),
        )
        if output is not None and len(filings) != 1:
            raise DatasetError(
                "--output requires a selection containing exactly one filing"
            )
        lock = load_lock(manifest.parent / "raw.lock.jsonl")
        missing_lock_ids = [
            filing.filing_id for filing in filings if filing.filing_id not in lock
        ]
        if missing_lock_ids:
            raise DatasetError("no raw lock entry for: " + ", ".join(missing_lock_ids))
    except DatasetError as error:
        _abort(error)

    failures: list[tuple[str, str]] = []
    normalized_count = 0
    normalization_application = FilingNormalizationApplication(
        FileSystemRawFilingLoader(manifest.parent)
    )
    for filing in filings:
        try:
            result = normalization_application.normalize(
                filing,
                expected_sha256=lock[filing.filing_id].sha256,
            )
            document = result.document
            normalized_path = output or (
                manifest.parent / "normalized" / f"{filing.filing_id}.json"
            )
            write_normalized(document, normalized_path)
            if output is None:
                update_normalized_manifest(
                    dataset_dir=manifest.parent,
                    normalized_path=normalized_path,
                    document=document,
                )
            normalized_count += 1
            typer.echo(
                f"Normalized draft: {normalized_path} "
                f"({len(document['sections'])} sections, "
                f"{len(document['blocks'])} blocks)"
            )
            if show_diagnostics:
                for diagnostic in result.diagnostics:
                    details = ", ".join(
                        f"{key}={value}"
                        for key, value in sorted(diagnostic.details.items())
                    )
                    typer.echo(f"  {diagnostic.stage}: {diagnostic.code} ({details})")
        except (ArtifactError, NormalizationError) as error:
            failures.append((filing.filing_id, str(error)))
            typer.echo(f"error: {filing.filing_id}: {error}")

    if output is None and normalized_count:
        typer.echo(
            "Normalized manifest: "
            f"{manifest.parent / 'normalized' / 'manifest.jsonl'}"
        )
    typer.echo(
        f"Normalization complete: {normalized_count} succeeded, "
        f"{len(failures)} failed"
    )
    if failures:
        raise typer.Exit(code=2)


@app.command()
def accept_normalized(
    filing_id: Annotated[
        str,
        typer.Option(help="Filing ID of the normalized artifact to accept."),
    ],
    reviewer: Annotated[
        str,
        typer.Option(help="Name or identifier of the human reviewer."),
    ],
    manifest: Annotated[
        Path,
        typer.Option(help="Path to the filing manifest."),
    ] = DEFAULT_MANIFEST,
) -> None:
    """Mark a reviewed normalized artifact as canonical in its manifest."""
    try:
        normalized_path = manifest.parent / "normalized" / f"{filing_id}.json"
        document = load_normalized(normalized_path)
        if document["filing_id"] != filing_id:
            raise ArtifactError("normalized artifact filing ID does not match its path")
        normalized_manifest = update_normalized_manifest(
            dataset_dir=manifest.parent,
            normalized_path=normalized_path,
            document=document,
            reviewer=reviewer,
        )
    except (ArtifactError, NormalizationError) as error:
        _abort(error)
    typer.echo(f"Accepted normalized reference: {filing_id} ({normalized_manifest})")


@app.command()
def render(
    input_path: Annotated[
        Path,
        typer.Option("--input", help="Path to normalized JSON."),
    ],
    manifest: Annotated[
        Path,
        typer.Option(help="Path to the filing manifest."),
    ] = DEFAULT_MANIFEST,
    output: Annotated[
        Path | None,
        typer.Option(
            help="Output HTML path; defaults to runs/rendered/<filing-id>.html."
        ),
    ] = None,
    compare_raw: Annotated[
        bool,
        typer.Option(
            help="Embed the local raw filing beside the normalized rendering."
        ),
    ] = False,
) -> None:
    """Render normalized JSON as safe, standalone HTML."""
    try:
        document = load_normalized(input_path)
        filing_id = str(document["filing_id"])
        rendered_path = output or Path("runs/rendered") / f"{filing_id}.html"
        raw_href = None
        if compare_raw:
            filing = _find_filing(load_manifest(manifest), filing_id)
            raw_path = manifest.parent / filing.raw_path
            if not raw_path.is_file():
                raise DatasetError(f"raw artifact not found: {raw_path}")
            raw_href = relative_href(raw_path, rendered_path)
        html = render_normalized_html(
            document,
            raw_href=raw_href,
            json_href=relative_href(input_path, rendered_path),
        )
        write_rendered_html(html, rendered_path)
    except (ArtifactError, DatasetError, NormalizationError) as error:
        _abort(error)
    typer.echo(f"Rendered normalized filing: {rendered_path}")
