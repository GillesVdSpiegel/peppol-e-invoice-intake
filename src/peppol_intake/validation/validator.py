"""Public validation entry point: XSD, then EN 16931, then Peppol."""

from __future__ import annotations

from pathlib import Path

from .artefacts import RULE_SETS, RuleSet
from .backends import DEFAULT_BACKEND, get_backend
from .backends.base import SchematronBackend
from .findings import ValidationResult
from .xsd import validate_xsd


def validate(
    document: Path,
    *,
    backend: str | SchematronBackend = DEFAULT_BACKEND,
    rule_sets: tuple[RuleSet, ...] = RULE_SETS,
    skip_xsd: bool = False,
) -> ValidationResult:
    """Validate a UBL invoice against every layer.

    A document that fails XSD validation short-circuits: Schematron rules assume a
    schema-valid tree, and running them over a structurally broken document
    produces a cascade of findings that describe the damage rather than the cause.
    """
    impl = get_backend(backend) if isinstance(backend, str) else backend

    if not skip_xsd:
        xsd_findings = validate_xsd(document)
        if xsd_findings:
            return ValidationResult(
                document=str(document), backend=impl.name, findings=xsd_findings
            )

    return ValidationResult(
        document=str(document),
        backend=impl.name,
        findings=impl.validate(document, rule_sets),
    )
