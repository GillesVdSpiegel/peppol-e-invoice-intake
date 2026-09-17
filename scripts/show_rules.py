"""Print the rule IDs each broken fixture actually trips.

Used to pin tests/broken_cases.py expectations to observed behaviour rather than
to assumptions about what the rule text says. Re-run after an artefact version
bump and review the diff.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from broken_cases import CASES  # noqa: E402
from peppol_intake.validation import validate  # noqa: E402


def main() -> int:
    backend = sys.argv[1] if len(sys.argv) > 1 else "saxon"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for case in CASES:
            result = validate(case.write(out), backend=backend)
            rules = sorted(result.rule_ids())
            warn = sorted(f.rule_id for f in result.warnings if f.rule_id)
            status = "OK" if rules else "!! NO BLOCKING FINDING"
            print(f"{case.id:<32} {status}")
            print(f"    blocking: {rules or '-'}")
            if warn:
                print(f"    warnings: {warn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
