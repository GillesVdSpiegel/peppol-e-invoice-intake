"""CLI behaviour: exit codes are the contract when this runs in a CI step."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from broken_cases import CASES_BY_ID, VALID_BASE
from conftest import requires_artefacts
from peppol_e_invoice_intake.cli import app

pytestmark = [pytest.mark.artefacts, requires_artefacts]

runner = CliRunner()


def test_valid_document_exits_zero():
    result = runner.invoke(app, ["check", str(VALID_BASE)])
    assert result.exit_code == 0, result.output


def test_invalid_document_exits_one(broken_dir: Path):
    result = runner.invoke(app, ["check", str(broken_dir / "missing-buyer-reference.xml")])
    assert result.exit_code == 1
    assert "PEPPOL-EN16931-R003" in result.output


def test_one_bad_document_fails_the_whole_run(broken_dir: Path):
    result = runner.invoke(
        app,
        ["check", str(VALID_BASE), str(broken_dir / "missing-buyer-reference.xml")],
    )
    assert result.exit_code == 1


def test_missing_file_is_reported_not_crashed(tmp_path: Path):
    result = runner.invoke(app, ["check", str(tmp_path / "nope.xml")])
    assert result.exit_code == 1
    assert "MISSING" in result.output


def test_unknown_backend_is_rejected():
    result = runner.invoke(app, ["check", str(VALID_BASE), "--backend", "nonsense"])
    assert result.exit_code != 0


def test_findings_with_non_cp1252_characters_render():
    """EN 16931 states arithmetic rules with a summation sign, which the default
    Windows console encoding cannot encode. Rendering must not crash on it."""
    case = CASES_BY_ID["tax-subtotal-miscalculated"]
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        document = case.write(Path(tmp))
        result = runner.invoke(app, ["check", str(document)])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "BR-CO-14" in result.output


def test_rules_command_lists_both_rule_sets():
    result = runner.invoke(app, ["rules"])
    assert result.exit_code == 0
    assert "production" in result.output
    assert "oracle" in result.output
