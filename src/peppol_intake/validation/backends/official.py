"""Oracle backend: OpenPEPPOL's own pre-compiled stylesheets.

This runs the exact XSLT the reference implementation executes, rather than a
stylesheet this project compiled. Pairing it against `SaxonBackend` in
tests/test_backend_agreement.py turns "our Schematron compilation is faithful"
from an assumption into a measurement.

What it does NOT independently verify: the XSLT runtime itself, since both
backends execute on Saxon. That limitation is stated in
docs/decisions/0001-dual-backend.md rather than papered over.
"""

from __future__ import annotations

from pathlib import Path

from ..artefacts import RuleSet
from ..findings import ValidationFinding
from ..saxon import xslt_processor
from ..svrl import parse_svrl
from .base import UnavailableBackend


class OfficialXsltBackend:
    name = "official"

    def available(self) -> bool:
        return self.unavailable_reason() is None

    def unavailable_reason(self) -> str | None:
        try:
            import saxonche  # noqa: F401
        except ImportError:
            return "saxonche is not installed (pip install saxonche)"
        return None

    def supports(self, rule_sets: tuple[RuleSet, ...]) -> bool:
        return all(rs.has_official_xslt for rs in rule_sets)

    def validate(
        self, document: Path, rule_sets: tuple[RuleSet, ...]
    ) -> tuple[ValidationFinding, ...]:
        if (reason := self.unavailable_reason()) is not None:
            raise UnavailableBackend(reason)

        findings: list[ValidationFinding] = []
        xp = xslt_processor()
        for rule_set in rule_sets:
            if not rule_set.has_official_xslt:
                raise UnavailableBackend(
                    f"No official stylesheet pinned for {rule_set.label} {rule_set.version}. "
                    "OpenPEPPOL only redistributes compiled stylesheets for some releases; "
                    "use ORACLE_RULE_SETS for cross-backend comparison."
                )
            executable = xp.compile_stylesheet(stylesheet_file=str(rule_set.official_xslt))
            svrl = executable.transform_to_string(source_file=str(document))
            if svrl is None:
                raise RuntimeError(f"{rule_set.label}: validation produced no SVRL for {document}")
            findings.extend(parse_svrl(svrl, rule_set.layer))
        return tuple(findings)
