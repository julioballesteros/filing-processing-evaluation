"""Command-line interface for the filing benchmark dataset."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from filing_processing_evaluation.dataset import (
    DatasetError,
    Filing,
    download_filings,
    load_lock,
    load_manifest,
    validate_dataset,
)
from filing_processing_evaluation.normalization import (
    load_normalized,
    normalize_filing,
    update_normalized_manifest,
    write_normalized,
)
from filing_processing_evaluation.rendering import (
    relative_href,
    render_normalized_html,
    write_rendered_html,
)

DEFAULT_MANIFEST = Path("dataset/manifest.jsonl")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="filing-processing-evaluation",
        description="Build and validate the filing-processing benchmark dataset.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="Validate the manifest, lock file, and optional raw files."
    )
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    validate.add_argument(
        "--check-raw",
        action="store_true",
        help="Require every selected raw file and verify it against the lock file.",
    )
    validate.add_argument("--form", choices=("10-K", "10-Q"))

    download = subparsers.add_parser(
        "download", help="Download raw primary documents from SEC EDGAR."
    )
    download.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    download.add_argument("--form", choices=("10-K", "10-Q"))
    download.add_argument(
        "--filing-id", action="append", default=[], help="Download one filing ID."
    )
    download.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT"),
        help="SEC-compliant identity; defaults to SEC_USER_AGENT.",
    )
    download.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Minimum delay between requests in seconds (default: 0.2).",
    )
    download.add_argument(
        "--force", action="store_true", help="Download files that already exist again."
    )

    normalize = subparsers.add_parser(
        "normalize",
        help=(
            "Create reviewable normalized drafts for selected locked raw filings; "
            "all manifest entries are selected by default."
        ),
    )
    normalize.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    normalize.add_argument("--form", choices=("10-K", "10-Q"))
    normalize.add_argument(
        "--filing-id",
        action="append",
        default=[],
        help="Normalize one filing ID; repeat to select multiple filings.",
    )
    normalize.add_argument(
        "--output",
        type=Path,
        help=(
            "Output JSON path when exactly one filing is selected; defaults to "
            "dataset/normalized/<filing-id>.json."
        ),
    )

    accept = subparsers.add_parser(
        "accept-normalized",
        help="Mark a reviewed normalized artifact as canonical in its manifest.",
    )
    accept.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    accept.add_argument("--filing-id", required=True)
    accept.add_argument(
        "--reviewer", required=True, help="Name or identifier of the human reviewer."
    )

    render = subparsers.add_parser(
        "render", help="Render normalized JSON as safe, standalone HTML."
    )
    render.add_argument("--input", type=Path, required=True)
    render.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    render.add_argument(
        "--output",
        type=Path,
        help="Output HTML path; defaults to runs/rendered/<filing-id>.html.",
    )
    render.add_argument(
        "--compare-raw",
        action="store_true",
        help="Embed the local raw filing beside the normalized rendering.",
    )
    return parser


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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            summary = validate_dataset(
                args.manifest, check_raw=args.check_raw, form_type=args.form
            )
            print(
                f"Valid dataset: {summary.total} filings "
                f"({summary.by_form['10-K']} 10-K, {summary.by_form['10-Q']} 10-Q)"
            )
            return 0

        if args.command == "normalize":
            entries = load_manifest(args.manifest)
            filings = _select_filings(
                entries,
                form_type=args.form,
                filing_ids=set(args.filing_id),
            )
            if args.output is not None and len(filings) != 1:
                raise DatasetError(
                    "--output requires a selection containing exactly one filing"
                )
            lock = load_lock(args.manifest.parent / "raw.lock.jsonl")
            missing_lock_ids = [
                filing.filing_id for filing in filings if filing.filing_id not in lock
            ]
            if missing_lock_ids:
                raise DatasetError(
                    "no raw lock entry for: " + ", ".join(missing_lock_ids)
                )

            failures: list[tuple[str, str]] = []
            normalized_count = 0
            for filing in filings:
                try:
                    document = normalize_filing(
                        filing,
                        dataset_dir=args.manifest.parent,
                        expected_sha256=lock[filing.filing_id].sha256,
                    )
                    output = args.output or (
                        args.manifest.parent / "normalized" / f"{filing.filing_id}.json"
                    )
                    write_normalized(document, output)
                    if args.output is None:
                        update_normalized_manifest(
                            dataset_dir=args.manifest.parent,
                            normalized_path=output,
                            document=document,
                        )
                    normalized_count += 1
                    print(
                        f"Normalized draft: {output} "
                        f"({len(document['sections'])} sections, "
                        f"{len(document['blocks'])} blocks)"
                    )
                except DatasetError as error:
                    failures.append((filing.filing_id, str(error)))
                    print(f"error: {filing.filing_id}: {error}")

            if args.output is None and normalized_count:
                print(
                    "Normalized manifest: "
                    f"{args.manifest.parent / 'normalized' / 'manifest.jsonl'}"
                )
            print(
                f"Normalization complete: {normalized_count} succeeded, "
                f"{len(failures)} failed"
            )
            return 2 if failures else 0

        if args.command == "accept-normalized":
            normalized_path = (
                args.manifest.parent / "normalized" / f"{args.filing_id}.json"
            )
            document = load_normalized(normalized_path)
            if document["filing_id"] != args.filing_id:
                raise DatasetError(
                    "normalized artifact filing ID does not match its path"
                )
            normalized_manifest = update_normalized_manifest(
                dataset_dir=args.manifest.parent,
                normalized_path=normalized_path,
                document=document,
                reviewer=args.reviewer,
            )
            print(
                f"Accepted normalized reference: {args.filing_id} "
                f"({normalized_manifest})"
            )
            return 0

        if args.command == "render":
            document = load_normalized(args.input)
            filing_id = str(document["filing_id"])
            output = args.output or Path("runs/rendered") / f"{filing_id}.html"
            raw_href = None
            if args.compare_raw:
                filing = _find_filing(load_manifest(args.manifest), filing_id)
                raw_path = args.manifest.parent / filing.raw_path
                if not raw_path.is_file():
                    raise DatasetError(f"raw artifact not found: {raw_path}")
                raw_href = relative_href(raw_path, output)
            html = render_normalized_html(
                document,
                raw_href=raw_href,
                json_href=relative_href(args.input, output),
            )
            write_rendered_html(html, output)
            print(f"Rendered normalized filing: {output}")
            return 0

        if not args.user_agent:
            raise DatasetError(
                "SEC_USER_AGENT is required. Use a value identifying your application "
                "and contact email, as required by SEC fair-access guidance."
            )
        entries = load_manifest(args.manifest)
        downloaded = download_filings(
            entries,
            dataset_dir=args.manifest.parent,
            user_agent=args.user_agent,
            form_type=args.form,
            filing_ids=set(args.filing_id),
            delay=args.delay,
            force=args.force,
        )
        print(f"Raw dataset ready: {downloaded} filing(s)")
        return 0
    except DatasetError as error:
        print(f"error: {error}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
