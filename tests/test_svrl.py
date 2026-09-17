"""Unit tests for SVRL parsing. No artefacts, no I/O."""

from __future__ import annotations

from peppol_intake.validation.findings import Layer, Severity
from peppol_intake.validation.svrl import parse_svrl

SVRL = """<?xml version="1.0"?>
<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl">
  <svrl:failed-assert test="cbc:EndpointID" location="/*:Invoice[1]"
                      id="PEPPOL-EN16931-R020" flag="fatal">
    <svrl:text>Seller electronic address MUST be provided</svrl:text>
  </svrl:failed-assert>
  <svrl:successful-report test="cbc:Note" location="/*:Invoice[1]"
                          id="PEPPOL-EN16931-R008" flag="warning">
    <svrl:text>Document should not contain
      empty elements</svrl:text>
  </svrl:successful-report>
  <svrl:failed-assert test="count(x)" location="/*:Invoice[1]" id="BR-01">
    <svrl:text>An Invoice shall have a Specification identifier</svrl:text>
  </svrl:failed-assert>
</svrl:schematron-output>
"""


def test_parses_both_failed_asserts_and_successful_reports():
    """Schematron `report` fires when a condition IS met; several Peppol rules are
    written that way, so dropping them would silently lose real errors."""
    findings = parse_svrl(SVRL, Layer.PEPPOL)
    assert {f.rule_id for f in findings} == {
        "PEPPOL-EN16931-R020",
        "PEPPOL-EN16931-R008",
        "BR-01",
    }


def test_flag_maps_to_severity():
    by_id = {f.rule_id: f for f in parse_svrl(SVRL, Layer.PEPPOL)}
    assert by_id["PEPPOL-EN16931-R020"].severity is Severity.FATAL
    assert by_id["PEPPOL-EN16931-R008"].severity is Severity.WARNING


def test_unflagged_assert_defaults_to_error_not_warning():
    """An unflagged rule must not be silently downgraded to advisory."""
    by_id = {f.rule_id: f for f in parse_svrl(SVRL, Layer.PEPPOL)}
    assert by_id["BR-01"].severity is Severity.ERROR
    assert by_id["BR-01"].is_blocking


def test_message_whitespace_is_normalised():
    by_id = {f.rule_id: f for f in parse_svrl(SVRL, Layer.PEPPOL)}
    assert by_id["PEPPOL-EN16931-R008"].message == "Document should not contain empty elements"


def test_location_and_test_are_preserved():
    by_id = {f.rule_id: f for f in parse_svrl(SVRL, Layer.PEPPOL)}
    assert by_id["PEPPOL-EN16931-R020"].location == "/*:Invoice[1]"
    assert by_id["PEPPOL-EN16931-R020"].test == "cbc:EndpointID"
