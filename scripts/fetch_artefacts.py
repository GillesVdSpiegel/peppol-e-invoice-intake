"""Download the pinned validation artefacts into artefacts/.

Rule sets are third-party and not all of them carry a licence that permits
redistribution, so this repository pins them by URL + SHA-256 in
artefacts/MANIFEST.json instead of vendoring the files. See artefacts/NOTICE.md.

    python scripts/fetch_artefacts.py                   # fetch + verify
    python scripts/fetch_artefacts.py --update-manifest  # re-pin (maintainer only)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTEFACTS = ROOT / "artefacts"
MANIFEST = ARTEFACTS / "MANIFEST.json"

# --- pinned upstream versions -------------------------------------------------
# PRODUCTION: the current Peppol BIS Billing release. The CEN and Peppol
# Schematron are taken from the SAME tag on purpose - a Peppol release bundles
# the exact CEN/TC 434 version it was tested against, so pairing them from one
# tag keeps the two layers consistent.
PRODUCTION_VERSION = "3.0.20"
PRODUCTION_TAG = "v3.0.20"

# ORACLE: the newest Billing release for which OpenPEPPOL's own pre-compiled
# stylesheets are redistributed (phive-rules, Apache-2.0). The backend-agreement
# test runs at this version on both sides, so it compares compilers rather than
# rule versions. See docs/decisions/0001-dual-backend.md.
ORACLE_VERSION = "3.0.18"
ORACLE_TAG = "3.0.18"
ORACLE_RELEASE = "2024.11"  # how phive-rules names the same release

SKELETON_TAG = "2020-10-01"
UBL_BASE = "https://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/"
PHIVE_RULES_JAR = (
    "https://repo1.maven.org/maven2/com/helger/phive/rules/phive-rules-peppol/"
    "3.2.10/phive-rules-peppol-3.2.10.jar"
)

SCH_NAMES = ("CEN-EN16931-UBL.sch", "PEPPOL-EN16931-UBL.sch")
SKELETON_NAMES = (
    "iso_dsdl_include.xsl",
    "iso_abstract_expand.xsl",
    "iso_svrl_for_xslt2.xsl",
    "iso_schematron_skeleton_for_saxon.xsl",
    "iso_schematron_message_xslt2.xsl",
)


def _peppol_raw(tag: str, name: str) -> str:
    return (
        f"https://raw.githubusercontent.com/OpenPEPPOL/peppol-bis-invoice-3/{tag}/rules/sch/{name}"
    )


FLAT_SOURCES: list[tuple[str, str]] = [
    *[
        (
            f"skeleton/{name}",
            f"https://raw.githubusercontent.com/Schematron/schematron/{SKELETON_TAG}"
            f"/trunk/schematron/code/{name}",
        )
        for name in SKELETON_NAMES
    ],
    *[(f"sch/{PRODUCTION_VERSION}/{n}", _peppol_raw(PRODUCTION_TAG, n)) for n in SCH_NAMES],
    *[(f"sch/{ORACLE_VERSION}/{n}", _peppol_raw(ORACLE_TAG, n)) for n in SCH_NAMES],
]

# Files lifted out of the phive-rules jar: OpenPEPPOL's own compiled stylesheets,
# i.e. what the reference implementation actually executes.
JAR_MEMBERS: list[tuple[str, str]] = [
    (
        f"official-xslt/{ORACLE_VERSION}/{name.replace('.sch', '.xslt')}",
        f"external/schematron/openpeppol/{ORACLE_RELEASE}/xslt/{name.replace('.sch', '.xslt')}",
    )
    for name in SCH_NAMES
]

XSD_ROOTS = ["maindoc/UBL-Invoice-2.1.xsd"]


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "peppol-intake/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - pinned https
        return resp.read()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _schema_locations(data: bytes) -> list[str]:
    from lxml import etree

    root = etree.fromstring(data)
    xs = "{http://www.w3.org/2001/XMLSchema}"
    out = []
    for tag in ("import", "include"):
        for el in root.iter(f"{xs}{tag}"):
            loc = el.get("schemaLocation")
            if loc and not loc.startswith(("http://", "https://")):
                out.append(loc)
    return out


def crawl_xsd() -> dict[str, tuple[str, bytes]]:
    """Return {dest: (url, content)} for the full XSD import closure."""
    seen: dict[str, tuple[str, bytes]] = {}
    queue = list(XSD_ROOTS)
    while queue:
        rel = urllib.parse.urljoin("/", queue.pop()).lstrip("/")
        dest = f"xsd/{rel}"
        if dest in seen:
            continue
        url = urllib.parse.urljoin(UBL_BASE, rel)
        data = _get(url)
        seen[dest] = (url, data)
        base_dir = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
        for loc in _schema_locations(data):
            queue.append(urllib.parse.urljoin("/" + base_dir, loc).lstrip("/"))
    return seen


def from_jar() -> dict[str, tuple[str, bytes]]:
    archive = zipfile.ZipFile(io.BytesIO(_get(PHIVE_RULES_JAR)))
    out = {}
    for dest, member in JAR_MEMBERS:
        try:
            out[dest] = (f"{PHIVE_RULES_JAR}!/{member}", archive.read(member))
        except KeyError:
            raise SystemExit(f"phive-rules jar no longer contains {member}") from None
    return out


def collect() -> dict[str, tuple[str, bytes]]:
    out: dict[str, tuple[str, bytes]] = {}
    for dest, url in FLAT_SOURCES:
        out[dest] = (url, _get(url))
    out.update(from_jar())
    out.update(crawl_xsd())
    return out


def write(files: dict[str, tuple[str, bytes]]) -> None:
    for dest, (_url, data) in files.items():
        path = ARTEFACTS / dest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--update-manifest",
        action="store_true",
        help="re-pin checksums from upstream (maintainer action; review the diff)",
    )
    args = ap.parse_args()

    print(f"Fetching artefacts (production={PRODUCTION_VERSION}, oracle={ORACLE_VERSION})...")
    files = collect()

    if args.update_manifest:
        manifest = {
            "production_version": PRODUCTION_VERSION,
            "oracle_version": ORACLE_VERSION,
            "skeleton_tag": SKELETON_TAG,
            "files": {
                dest: {"url": url, "sha256": _digest(data)}
                for dest, (url, data) in sorted(files.items())
            },
        }
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        write(files)
        print(f"Wrote {MANIFEST.relative_to(ROOT)} with {len(files)} entries.")
        return 0

    if not MANIFEST.exists():
        print("No MANIFEST.json; run with --update-manifest first.", file=sys.stderr)
        return 1

    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    problems = []
    for missing in sorted(set(expected) - set(files)):
        problems.append(f"upstream no longer provides {missing}")
    for extra in sorted(set(files) - set(expected)):
        problems.append(f"upstream added unpinned file {extra}")
    for dest, (_url, data) in sorted(files.items()):
        if dest in expected and (got := _digest(data)) != expected[dest]["sha256"]:
            problems.append(f"{dest}: sha256 {got} != pinned {expected[dest]['sha256']}")

    if problems:
        print("Artefact verification FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nUpstream changed under a pinned tag. Review, then --update-manifest.",
            file=sys.stderr,
        )
        return 1

    write(files)
    print(f"OK: {len(files)} artefacts verified against MANIFEST.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
