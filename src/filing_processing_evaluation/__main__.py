"""Command-line interface for the filing benchmark dataset."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from filing_processing_evaluation.dataset import (
    DatasetError,
    download_filings,
    load_manifest,
    validate_dataset,
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
    return parser


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
