"""The corpus builder: deterministic assignment, and the refusal to ship bad data."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import requires_artefacts
from peppol_e_invoice_intake.corpus.build import (
    CorpusBuildError,
    build,
    layouts_for,
    load_manifest,
)
from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.corpus.model import Invoice
from peppol_e_invoice_intake.corpus.render import LAYOUTS
from test_corpus_model import CUSTOMER, SUPPLIER, line


def test_layout_assignment_is_deterministic():
    assert [layout.name for layout in layouts_for(3)] == [
        layout.name for layout in layouts_for(3)
    ]


def test_layout_assignment_picks_distinct_layouts():
    for index in range(len(LAYOUTS) * 3):
        names = [layout.name for layout in layouts_for(index)]
        assert len(set(names)) == len(names), f"index {index} repeats a layout"


def test_layout_assignment_is_balanced_across_the_catalogue():
    counts = Counter(
        layout.name for index in range(len(CATALOGUE)) for layout in layouts_for(index)
    )
    assert set(counts) == {layout.name for layout in LAYOUTS}
    assert max(counts.values()) - min(counts.values()) <= 2, counts


def test_asking_for_more_layouts_than_exist_is_rejected():
    with pytest.raises(ValueError):
        layouts_for(0, len(LAYOUTS) + 1)


def _tiny_catalogue() -> dict[str, Invoice]:
    return {
        "one": Invoice(
            number="T-1",
            issue_date=date(2026, 3, 2),
            due_date=date(2026, 4, 1),
            supplier=SUPPLIER,
            customer=CUSTOMER,
            buyer_reference="REF-1",
            lines=[line(unit_price=Decimal("100.00"))],
        )
    }


@pytest.mark.artefacts
@requires_artefacts
def test_build_writes_ubl_ground_truth_and_a_manifest(tmp_path: Path):
    documents = build(tmp_path, catalogue=_tiny_catalogue(), render_pdfs=False)

    assert len(documents) == 3
    assert (tmp_path / "ubl" / "one.xml").exists()
    assert (tmp_path / "ground-truth" / "one.json").exists()

    manifest = load_manifest(tmp_path)
    assert manifest["base_invoices"] == 1
    assert manifest["documents"] == 3
    assert len({entry["layout"] for entry in manifest["entries"]}) == 3


@pytest.mark.artefacts
@requires_artefacts
def test_ground_truth_is_shared_across_layouts_of_one_invoice(tmp_path: Path):
    """Layout changes how a document looks, never what it says."""
    build(tmp_path, catalogue=_tiny_catalogue(), render_pdfs=False)
    entries = load_manifest(tmp_path)["entries"]
    assert len({entry["ground_truth"] for entry in entries}) == 1
    assert len({entry["pdf"] for entry in entries}) == 3


@pytest.mark.artefacts
@requires_artefacts
def test_ground_truth_on_disk_matches_the_emitted_ubl(tmp_path: Path):
    from peppol_e_invoice_intake.corpus.fields import extract_ground_truth

    build(tmp_path, catalogue=_tiny_catalogue(), render_pdfs=False)
    written = json.loads((tmp_path / "ground-truth" / "one.json").read_text(encoding="utf-8"))
    recomputed = extract_ground_truth((tmp_path / "ubl" / "one.xml").read_bytes())
    assert written == recomputed


@pytest.mark.artefacts
@requires_artefacts
def test_an_invalid_invoice_fails_the_build_and_leaves_nothing_behind(tmp_path: Path):
    """A corpus that is not Peppol-compliant would make every downstream number
    meaningless, so an invalid document is a build failure, not a warning."""
    broken = Invoice(
        number="T-BAD",
        issue_date=date(2026, 3, 2),
        supplier=SUPPLIER,
        customer=CUSTOMER,
        # No buyer reference, no order reference and no due date or payment terms:
        # trips PEPPOL-EN16931-R003 and BR-CO-25.
        lines=[line(unit_price=Decimal("100.00"))],
    )

    with pytest.raises(CorpusBuildError) as excinfo:
        build(tmp_path, catalogue={"broken": broken}, render_pdfs=False)

    assert "broken" in str(excinfo.value)
    assert not (tmp_path / "ubl" / "broken.xml").exists()
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.artefacts
@requires_artefacts
def test_full_renders_every_layout(tmp_path: Path):
    documents = build(tmp_path, catalogue=_tiny_catalogue(), full=True, render_pdfs=False)
    assert len(documents) == len(LAYOUTS)


@pytest.mark.artefacts
@requires_artefacts
def test_manifest_paths_are_relative_and_use_forward_slashes(tmp_path: Path):
    """The manifest is read on whatever machine runs the evaluation, so it must
    not carry absolute Windows paths."""
    build(tmp_path, catalogue=_tiny_catalogue(), render_pdfs=False)
    for entry in load_manifest(tmp_path)["entries"]:
        for key in ("pdf", "ubl", "ground_truth"):
            assert not Path(entry[key]).is_absolute()
            assert "\\" not in entry[key]


def test_load_manifest_explains_itself_when_the_corpus_is_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="corpus build"):
        load_manifest(tmp_path)


@pytest.mark.artefacts
@requires_artefacts
@pytest.mark.pdf
def test_pdfs_are_produced_for_the_real_catalogue_sample(tmp_path: Path):
    playwright = pytest.importorskip("playwright.sync_api")
    sample = dict(list(CATALOGUE.items())[:2])
    try:
        documents = build(tmp_path, catalogue=sample, layouts_per_invoice=2)
    except playwright.Error as exc:  # pragma: no cover - depends on local install
        pytest.skip(f"Chromium not installed: {exc}")

    assert len(documents) == 4
    for document in documents:
        assert document.pdf.exists()
        assert document.pdf.read_bytes().startswith(b"%PDF-")
