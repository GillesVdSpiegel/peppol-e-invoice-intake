"""Unit tests for the findings model. No artefacts, no I/O."""

from __future__ import annotations

from peppol_intake.validation.findings import (
    Layer,
    Severity,
    ValidationFinding,
    ValidationResult,
)


def finding(**kwargs) -> ValidationFinding:
    return ValidationFinding(
        **{
            "layer": Layer.PEPPOL,
            "severity": Severity.FATAL,
            "message": "boom",
            "rule_id": "PEPPOL-EN16931-R001",
            **kwargs,
        }
    )


def test_warnings_do_not_make_a_document_invalid():
    result = ValidationResult(
        document="x.xml", backend="saxon", findings=(finding(severity=Severity.WARNING),)
    )
    assert result.is_valid
    assert result.warnings and not result.blocking


def test_a_fatal_finding_makes_a_document_invalid():
    result = ValidationResult(document="x.xml", backend="saxon", findings=(finding(),))
    assert not result.is_valid


def test_identity_ignores_message_and_location():
    """Backends word messages differently; identity must not depend on wording."""
    a = finding(message="Seller endpoint missing", location="/Invoice[1]")
    b = finding(message="[PEPPOL-EN16931-R020] endpoint absent", location=None)
    assert a.identity == b.identity


def test_rule_ids_defaults_to_blocking_only():
    result = ValidationResult(
        document="x.xml",
        backend="saxon",
        findings=(
            finding(rule_id="BR-01"),
            finding(rule_id="PEPPOL-EN16931-R010", severity=Severity.WARNING),
        ),
    )
    assert result.rule_ids() == {"BR-01"}
    assert result.rule_ids(blocking_only=False) == {"BR-01", "PEPPOL-EN16931-R010"}


def test_for_layer_partitions_findings():
    result = ValidationResult(
        document="x.xml",
        backend="saxon",
        findings=(finding(layer=Layer.EN16931, rule_id="BR-01"), finding()),
    )
    assert len(result.for_layer(Layer.EN16931)) == 1
    assert len(result.for_layer(Layer.PEPPOL)) == 1
    assert result.for_layer(Layer.XSD) == ()
