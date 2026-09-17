"""Both backends must report the same rules for the same document.

This is what makes "the harness is correct" a measurement rather than an
assumption: `saxon` runs stylesheets this project compiled from Schematron
source, `official` runs the stylesheets OpenPEPPOL compiled and ships. If our
compilation were subtly wrong - a dropped xsl:function, a missing allow-foreign,
an unsupported query binding - the two would diverge here.

Both sides run at ORACLE_VERSION so this compares compilers, not rule versions.
See docs/decisions/0001-dual-backend.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from broken_cases import CASES, VALID_BASE
from conftest import requires_oracle
from peppol_intake.validation.artefacts import ORACLE_RULE_SETS
from peppol_intake.validation.backends import get_backend

pytestmark = [pytest.mark.artefacts, requires_oracle]

DOCUMENTS = [("valid-base", None), *[(c.id, c) for c in CASES]]


def _document(case, broken_dir: Path) -> Path:
    return VALID_BASE if case is None else broken_dir / f"{case.id}.xml"


@pytest.mark.parametrize(("name", "case"), DOCUMENTS, ids=[n for n, _ in DOCUMENTS])
def test_backends_agree(name: str, case, broken_dir: Path):
    document = _document(case, broken_dir)
    ours = get_backend("saxon").validate(document, ORACLE_RULE_SETS)
    theirs = get_backend("official").validate(document, ORACLE_RULE_SETS)

    ours_only = {f.identity for f in ours} - {f.identity for f in theirs}
    theirs_only = {f.identity for f in theirs} - {f.identity for f in ours}
    assert not (ours_only or theirs_only), (
        f"{name}: backends disagree\n"
        f"  only from our compile:     {sorted(ours_only)}\n"
        f"  only from official XSLT:   {sorted(theirs_only)}"
    )


def test_agreement_suite_is_not_vacuous(broken_dir: Path):
    """Guard against the comparison passing because both sides return nothing."""
    total = 0
    for _name, case in DOCUMENTS:
        if case is None:
            continue
        document = _document(case, broken_dir)
        total += len(get_backend("official").validate(document, ORACLE_RULE_SETS))
    assert total >= len(CASES), "oracle backend produced suspiciously few findings"
