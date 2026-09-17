"""In-process Schematron backend built on SaxonC-HE."""

from __future__ import annotations

from pathlib import Path

from ..artefacts import RuleSet, artefacts_available, require_artefacts
from ..compile import compile_schematron
from ..findings import ValidationFinding
from ..saxon import xslt_processor
from ..svrl import parse_svrl
from .base import UnavailableBackend


class SaxonBackend:
    name = "saxon"

    def available(self) -> bool:
        return self.unavailable_reason() is None

    def unavailable_reason(self) -> str | None:
        try:
            import saxonche  # noqa: F401
        except ImportError:
            return "saxonche is not installed (pip install saxonche)"
        if not artefacts_available():
            return "validation artefacts not fetched (python scripts/fetch_artefacts.py)"
        return None

    def validate(
        self, document: Path, rule_sets: tuple[RuleSet, ...]
    ) -> tuple[ValidationFinding, ...]:
        if (reason := self.unavailable_reason()) is not None:
            raise UnavailableBackend(reason)
        require_artefacts(rule_sets)

        findings: list[ValidationFinding] = []
        xp = xslt_processor()
        for rule_set in rule_sets:
            stylesheet = compile_schematron(rule_set.sch, rule_set.compiled)
            executable = xp.compile_stylesheet(stylesheet_file=str(stylesheet))
            svrl = executable.transform_to_string(source_file=str(document))
            if svrl is None:
                raise RuntimeError(f"{rule_set.label}: validation produced no SVRL for {document}")
            findings.extend(parse_svrl(svrl, rule_set.layer))
        return tuple(findings)
