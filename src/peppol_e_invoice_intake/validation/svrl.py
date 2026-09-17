"""Turn SVRL (Schematron Validation Report Language) output into findings."""

from __future__ import annotations

from lxml import etree

from .findings import Layer, Severity, ValidationFinding

SVRL_NS = "http://purl.oclc.org/dsdl/svrl"
_FAILED_ASSERT = f"{{{SVRL_NS}}}failed-assert"
_SUCCESSFUL_REPORT = f"{{{SVRL_NS}}}successful-report"
_TEXT = f"{{{SVRL_NS}}}text"

# Peppol and CEN both express severity through @flag. Anything unflagged is
# treated as an error rather than silently downgraded to a warning.
_FLAGS = {
    "fatal": Severity.FATAL,
    "error": Severity.ERROR,
    "warning": Severity.WARNING,
    "warn": Severity.WARNING,
    "info": Severity.WARNING,
}


def _severity(element: etree._Element) -> Severity:
    raw = (element.get("flag") or element.get("role") or "").strip().lower()
    return _FLAGS.get(raw, Severity.ERROR)


def _message(element: etree._Element) -> str:
    text_el = element.find(_TEXT)
    source = text_el if text_el is not None else element
    return " ".join("".join(source.itertext()).split())


def parse_svrl(svrl: str | bytes, layer: Layer) -> tuple[ValidationFinding, ...]:
    """Extract findings from an SVRL report.

    Both failed-assert and successful-report are collected: Schematron uses
    `report` for rules that fire when a condition IS met, and several Peppol
    rules are written that way, so ignoring them would silently drop real errors.
    """
    data = svrl.encode("utf-8") if isinstance(svrl, str) else svrl
    root = etree.fromstring(data)

    findings = []
    for element in root.iter(_FAILED_ASSERT, _SUCCESSFUL_REPORT):
        findings.append(
            ValidationFinding(
                layer=layer,
                severity=_severity(element),
                message=_message(element),
                rule_id=element.get("id") or None,
                location=element.get("location") or None,
                test=element.get("test") or None,
            )
        )
    return tuple(findings)
