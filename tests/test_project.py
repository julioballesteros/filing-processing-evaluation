"""Smoke tests for filing-processing-evaluation."""

import pytest

from filing_processing_evaluation.__main__ import main


def test_main(capsys: pytest.CaptureFixture[str]) -> None:
    """The command-line entry point prints a greeting."""
    main()

    assert capsys.readouterr().out == "Hello from filing-processing-evaluation!\n"
