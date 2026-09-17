"""UBL 2.1 XSD validation: the structural layer, run before any Schematron.

A document that is not schema-valid will produce noisy, misleading Schematron
output, so this layer short-circuits the rest of the pipeline.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

from .artefacts import INVOICE_XSD, require_artefacts
from .findings import Layer, Severity, ValidationFinding


@lru_cache(maxsize=1)
def _schema() -> etree.XMLSchema:
    require_artefacts()
    return etree.XMLSchema(etree.parse(str(INVOICE_XSD)))


def validate_xsd(document: Path) -> tuple[ValidationFinding, ...]:
    try:
        tree = etree.parse(str(document))
    except etree.XMLSyntaxError as exc:
        return (
            ValidationFinding(
                layer=Layer.XSD,
                severity=Severity.FATAL,
                message=f"Document is not well-formed XML: {exc.msg}",
                rule_id="XML-NOT-WELL-FORMED",
                location=str(document),
            ),
        )

    schema = _schema()
    if schema.validate(tree):
        return ()

    return tuple(
        ValidationFinding(
            layer=Layer.XSD,
            severity=Severity.FATAL,
            message=error.message,
            rule_id="XSD-SCHEMA-INVALID",
            location=error.path,
        )
        for error in schema.error_log
    )
