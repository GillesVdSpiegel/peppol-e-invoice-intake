"""Locations and versions of the pinned validation artefacts.

Artefacts are fetched by scripts/fetch_artefacts.py rather than vendored; see
artefacts/NOTICE.md for why (not every upstream carries a redistribution licence).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .findings import Layer

ROOT = Path(__file__).resolve().parents[3]
ARTEFACTS = ROOT / "artefacts"
SCH_DIR = ARTEFACTS / "sch"
SKELETON_DIR = ARTEFACTS / "skeleton"
XSD_DIR = ARTEFACTS / "xsd"
COMPILED_DIR = ARTEFACTS / "compiled"
OFFICIAL_XSLT_DIR = ARTEFACTS / "official-xslt"

INVOICE_XSD = XSD_DIR / "maindoc" / "UBL-Invoice-2.1.xsd"

#: Current Peppol BIS Billing release; what the pipeline validates against.
PRODUCTION_VERSION = "3.0.20"

#: Newest release for which OpenPEPPOL's own compiled stylesheets are
#: redistributable, so both backends can be compared at one common version.
ORACLE_VERSION = "3.0.18"


@dataclass(frozen=True)
class RuleSet:
    layer: Layer
    label: str
    version: str
    sch_name: str

    @property
    def sch(self) -> Path:
        return SCH_DIR / self.version / self.sch_name

    @property
    def compiled(self) -> Path:
        """Where our own skeleton-compiled stylesheet is cached."""
        return COMPILED_DIR / self.version / self.sch_name.replace(".sch", ".xslt")

    @property
    def official_xslt(self) -> Path:
        """OpenPEPPOL's own compiled stylesheet, when one is pinned for this version."""
        return OFFICIAL_XSLT_DIR / self.version / self.sch_name.replace(".sch", ".xslt")

    @property
    def has_official_xslt(self) -> bool:
        return self.official_xslt.exists()


def _pair(version: str) -> tuple[RuleSet, ...]:
    return (
        RuleSet(Layer.EN16931, "EN 16931 (CEN/TC 434)", version, "CEN-EN16931-UBL.sch"),
        RuleSet(Layer.PEPPOL, "Peppol BIS Billing 3.0", version, "PEPPOL-EN16931-UBL.sch"),
    )


RULE_SETS: tuple[RuleSet, ...] = _pair(PRODUCTION_VERSION)
ORACLE_RULE_SETS: tuple[RuleSet, ...] = _pair(ORACLE_VERSION)


class ArtefactsMissing(RuntimeError):
    """Raised when the pinned artefacts have not been fetched."""


def require_artefacts(rule_sets: tuple[RuleSet, ...] = RULE_SETS) -> None:
    required = [INVOICE_XSD, SKELETON_DIR / "iso_svrl_for_xslt2.xsl", *(r.sch for r in rule_sets)]
    if missing := [p for p in required if not p.exists()]:
        names = ", ".join(str(p.relative_to(ROOT)) for p in missing)
        raise ArtefactsMissing(
            f"Missing validation artefacts ({names}).\nRun: python scripts/fetch_artefacts.py"
        )


def artefacts_available(rule_sets: tuple[RuleSet, ...] = RULE_SETS) -> bool:
    try:
        require_artefacts(rule_sets)
    except ArtefactsMissing:
        return False
    return True
