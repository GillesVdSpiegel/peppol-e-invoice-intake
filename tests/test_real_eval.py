"""The real-invoice evaluation, exercised offline with stand-in documents."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import requires_artefacts
from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.corpus.fields import extract_ground_truth
from peppol_e_invoice_intake.corpus.ubl import to_xml
from peppol_e_invoice_intake.evaluation import EvalConfig
from peppol_e_invoice_intake.evaluation.real import (
    KEY_FIELDS,
    LabelError,
    evaluate_real,
    init_labels,
    label_path,
    labelled_documents,
    load_labels,
    score_real,
)
from peppol_e_invoice_intake.pipeline import Budget
from test_evaluation import ReadsTheDocument

CONFIG = EvalConfig(
    arm="vision", model="claude-opus-5", extract_effort="low", repair_effort="high",
    allow_repair=True, sample_size=0, selection="real",
)


def stand_in(root: Path, key: str) -> Path:
    """A PDF standing in for a real invoice; the fake client reads the key off it."""
    root.mkdir(parents=True, exist_ok=True)
    pdf = root / f"{key}.pdf"
    pdf.write_bytes(f"%PDF-1.4\n{key}\nreal\n".encode())
    return pdf


def label_honestly(pdf: Path, key: str, **overrides: str) -> None:
    """Fill a label file the way a person would, from what the invoice says."""
    truth = extract_ground_truth(to_xml(CATALOGUE[key]))["document"]
    data = json.loads(label_path(pdf).read_text(encoding="utf-8"))
    for bt in KEY_FIELDS:
        data["fields"][bt]["value"] = overrides.get(bt, truth.get(bt, ""))
    data["verified"] = True
    label_path(pdf).write_text(json.dumps(data), encoding="utf-8")


def test_init_writes_a_blank_template_for_each_pdf(tmp_path: Path):
    stand_in(tmp_path, "standard-single-rate")
    created = init_labels(tmp_path)

    (path,) = created
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["verified"] is False
    assert set(data["fields"]) == set(KEY_FIELDS)
    assert all(field["value"] == "" for field in data["fields"].values()), (
        "labels must start blank: pre-filling from the model would score it "
        "against its own answers"
    )


def test_init_never_overwrites_a_label_someone_has_typed(tmp_path: Path):
    pdf = stand_in(tmp_path, "standard-single-rate")
    init_labels(tmp_path)
    label_honestly(pdf, "standard-single-rate")
    before = label_path(pdf).read_text(encoding="utf-8")

    assert init_labels(tmp_path) == []
    assert label_path(pdf).read_text(encoding="utf-8") == before


def test_an_unverified_label_is_refused(tmp_path: Path):
    pdf = stand_in(tmp_path, "standard-single-rate")
    init_labels(tmp_path)
    with pytest.raises(LabelError, match="not marked verified"):
        load_labels(label_path(pdf))


def test_unverified_documents_are_skipped_and_named(tmp_path: Path):
    stand_in(tmp_path, "standard-single-rate")
    ready = stand_in(tmp_path, "multiple-vat-rates")
    init_labels(tmp_path)
    label_honestly(ready, "multiple-vat-rates")

    documents, skipped = labelled_documents(tmp_path)
    assert documents == [ready]
    assert any("standard-single-rate" in note for note in skipped)


def test_an_empty_label_means_the_invoice_does_not_print_it(tmp_path: Path):
    pdf = stand_in(tmp_path, "minimal-single-line")
    init_labels(tmp_path)
    label_honestly(pdf, "minimal-single-line")
    labels = load_labels(label_path(pdf))
    assert labels["BT-84"] is None  # this invoice carries no IBAN


def test_scoring_counts_misses_and_inventions_separately():
    xml = to_xml(CATALOGUE["standard-single-rate"])
    truth = extract_ground_truth(xml)["document"]
    labels = {bt: truth.get(bt) for bt in KEY_FIELDS}

    assert score_real(labels, xml)["accuracy"] == 1.0

    labels["BT-1"] = "SOMETHING-ELSE"
    labels["BT-84"] = None  # pretend the paper shows no IBAN
    score = score_real(labels, xml)
    assert [m["field"] for m in score["misses"]] == ["BT-1"]
    assert score["fields_spurious"] == 1


@pytest.mark.artefacts
@requires_artefacts
def test_a_real_run_scores_verified_invoices_and_resumes(tmp_path: Path):
    root = tmp_path / "real"
    for key in ("standard-single-rate", "reverse-charge-construction"):
        pdf = stand_in(root, key)
        init_labels(root)
        label_honestly(pdf, key)

    out = tmp_path / "run"
    summary = evaluate_real(out, CONFIG, budget=Budget(), root=root,
                            client=ReadsTheDocument())
    assert summary["documents"] == 2
    assert summary["field_accuracy"] == 1.0
    assert summary["fields_spurious"] == 0

    again = ReadsTheDocument()
    evaluate_real(out, CONFIG, budget=Budget(), root=root, client=again)
    assert again.calls == [], "a finished real run must not pay again"


@pytest.mark.artefacts
@requires_artefacts
def test_a_label_the_pipeline_disagrees_with_is_reported_as_a_miss(tmp_path: Path):
    root = tmp_path / "real"
    pdf = stand_in(root, "standard-single-rate")
    init_labels(root)
    label_honestly(pdf, "standard-single-rate", **{"BT-115": "9999.99"})

    summary = evaluate_real(tmp_path / "run", CONFIG, budget=Budget(limit_usd=Decimal("1")),
                            root=root, client=ReadsTheDocument())
    assert summary["misses_by_field"]["BT-115"] == 1


def test_no_verified_labels_is_an_error_not_an_empty_success(tmp_path: Path):
    stand_in(tmp_path / "real", "standard-single-rate")
    with pytest.raises(LabelError, match="no verified"):
        evaluate_real(tmp_path / "run", CONFIG, budget=Budget(), root=tmp_path / "real")


def test_real_invoices_can_never_be_committed():
    """They are other people's commercial data."""
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "corpus/real/some-supplier.pdf"],
        capture_output=True, check=False,
    )
    assert result.returncode == 0
