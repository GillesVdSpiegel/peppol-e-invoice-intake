"""The Phase 1 contract: known-good passes, known-bad fails on the expected rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from broken_cases import CASES, VALID_BASE
from conftest import requires_artefacts
from peppol_intake.validation import Layer, validate
from peppol_intake.validation.xsd import validate_xsd

pytestmark = [pytest.mark.artefacts, requires_artefacts]


def test_valid_fixture_passes_every_layer():
    result = validate(VALID_BASE)
    assert result.is_valid, "\n".join(str(f) for f in result.blocking)
    assert result.findings == (), "the valid base should be clean, not merely non-blocking"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_broken_case_trips_exactly_its_expected_rules(case, broken_dir: Path):
    """A fixture that trips the WRONG rule fails as loudly as one that passes.

    Expectations are pinned to observed validator output (scripts/show_rules.py),
    so an artefact version bump that changes which rule fires shows up here rather
    than silently shifting the accuracy numbers this project publishes.
    """
    result = validate(broken_dir / f"{case.id}.xml")
    assert result.rule_ids() == case.expected_rules, (
        f"{case.id}: {case.description}\n"
        f"  expected {sorted(case.expected_rules)}\n"
        f"  observed {sorted(result.rule_ids())}"
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_broken_case_is_reported_invalid(case, broken_dir: Path):
    assert not validate(broken_dir / f"{case.id}.xml").is_valid


def test_belgian_enterprise_number_check_is_active():
    """The 0208 mod-97 rule is Belgium-specific and central to this project;
    assert it explicitly rather than relying on the parametrised sweep."""
    case = next(c for c in CASES if c.id == "bad-belgian-enterprise-number")
    assert "PEPPOL-COMMON-R043" in case.expected_rules


def test_both_rule_layers_are_actually_running():
    """Peppol rejects things EN 16931 accepts. If only one layer were wired up the
    suite could still look green, so assert findings come from both."""
    en_rules = {r for c in CASES for r in c.expected_rules if r.startswith("BR-")}
    peppol_rules = {r for c in CASES for r in c.expected_rules if r.startswith("PEPPOL-")}
    assert en_rules, "no EN 16931 rule is exercised by any fixture"
    assert peppol_rules, "no Peppol rule is exercised by any fixture"


def test_findings_are_tagged_with_their_layer(broken_dir: Path):
    result = validate(broken_dir / "missing-customization-id.xml")
    assert {f.layer for f in result.findings} == {Layer.EN16931, Layer.PEPPOL}


def test_malformed_xml_is_reported_not_raised(tmp_path: Path):
    doc = tmp_path / "broken.xml"
    doc.write_text("<Invoice><unclosed>", encoding="utf-8")
    result = validate(doc)
    assert not result.is_valid
    assert result.rule_ids() == {"XML-NOT-WELL-FORMED"}


def test_xsd_failure_short_circuits_schematron(tmp_path: Path):
    """Schematron over a schema-invalid tree describes the damage, not the cause."""
    doc = tmp_path / "not-ubl.xml"
    doc.write_text('<?xml version="1.0"?><NotAnInvoice/>', encoding="utf-8")
    result = validate(doc)
    assert not result.is_valid
    assert {f.layer for f in result.findings} == {Layer.XSD}


def test_valid_fixture_is_schema_valid():
    assert validate_xsd(VALID_BASE) == ()
