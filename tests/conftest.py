from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from peppol_e_invoice_intake.validation.artefacts import (  # noqa: E402
    ORACLE_RULE_SETS,
    RULE_SETS,
    artefacts_available,
)

requires_artefacts = pytest.mark.skipif(
    not artefacts_available(RULE_SETS),
    reason="validation artefacts not fetched (python scripts/fetch_artefacts.py)",
)

requires_oracle = pytest.mark.skipif(
    not (
        artefacts_available(ORACLE_RULE_SETS)
        and all(r.has_official_xslt for r in ORACLE_RULE_SETS)
    ),
    reason="oracle artefacts not fetched (python scripts/fetch_artefacts.py)",
)


@pytest.fixture(scope="session")
def broken_dir(tmp_path_factory) -> Path:
    """Materialise every broken case once per session."""
    from broken_cases import CASES

    directory = tmp_path_factory.mktemp("broken")
    for case in CASES:
        case.write(directory)
    return directory
