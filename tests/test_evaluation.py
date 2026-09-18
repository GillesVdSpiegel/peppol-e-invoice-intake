"""Scoring, sampling and the evaluation runner. No network, no spend."""

from __future__ import annotations

import base64
import json
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import requires_artefacts
from fake_anthropic import FakeCall, FakeResponse, FakeStream, FakeUsage
from peppol_e_invoice_intake.corpus.build import build, layouts_for
from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.corpus.fields import extract_ground_truth
from peppol_e_invoice_intake.corpus.render import LAYOUTS
from peppol_e_invoice_intake.corpus.ubl import to_xml
from peppol_e_invoice_intake.evaluation import (
    PRIORITY,
    ConfigMismatch,
    EvalConfig,
    evaluate,
    score_document,
    select_sample,
)
from peppol_e_invoice_intake.pipeline import Budget
from perfect_extraction import perfect_extraction

# --- scoring ------------------------------------------------------------------


def truth_of(key: str) -> dict:
    return extract_ground_truth(to_xml(CATALOGUE[key]))


def test_a_perfect_prediction_scores_every_field():
    truth = truth_of("multiple-vat-rates")
    score = score_document(truth, truth)
    assert score.overall.accuracy == 1.0
    assert score.overall.spurious == 0
    assert score.misses == []


def test_no_prediction_at_all_scores_zero_not_an_error():
    score = score_document(truth_of("standard-single-rate"), None)
    assert score.overall.accuracy == 0.0
    assert score.overall.total > 20


def test_a_single_wrong_field_is_named():
    truth = truth_of("standard-single-rate")
    predicted = deepcopy(truth)
    predicted["document"]["BT-1"] = "2026-0009"

    score = score_document(truth, predicted)
    assert score.by_field["BT-1"].correct == 0
    (miss,) = score.misses
    assert miss["field"] == "BT-1"
    assert miss["expected"] == truth["document"]["BT-1"]


def test_an_invented_field_is_spurious_not_a_miss():
    """Inventing and missing are different failures; folding them together would
    hide the one this project most wants to avoid."""
    truth = truth_of("standard-single-rate")
    assert "BT-22" not in truth["document"], "this invoice carries no note"
    predicted = deepcopy(truth)
    predicted["document"]["BT-22"] = "A note the document never printed"

    score = score_document(truth, predicted)
    assert score.overall.accuracy == 1.0
    assert score.by_field["BT-22"].spurious == 1


def test_amounts_compare_as_numbers():
    truth = truth_of("standard-single-rate")
    predicted = deepcopy(truth)
    predicted["document"]["BT-115"] = predicted["document"]["BT-115"].rstrip("0")
    assert score_document(truth, predicted).overall.accuracy == 1.0


def test_a_dropped_line_costs_every_line_after_it():
    """Positional alignment is deliberate: dropping a line really does misplace
    everything after it, and the score should say so."""
    truth = truth_of("multiple-vat-rates")
    predicted = deepcopy(truth)
    del predicted["lines"][0]

    score = score_document(truth, predicted)
    line_fields = [m for m in score.misses if m["where"].startswith("line")]
    assert len({m["where"] for m in line_fields}) == len(truth["lines"])


def test_vat_rows_align_by_category_and_rate_not_position():
    truth = truth_of("multiple-vat-rates")
    predicted = deepcopy(truth)
    predicted["vat_breakdown"].reverse()
    assert score_document(truth, predicted).overall.accuracy == 1.0


# --- sampling -----------------------------------------------------------------


def manifest_like_entries() -> list[dict]:
    return [
        {"key": key, "layout": layout.name, "language": invoice.language.value,
         "pdf": f"pdf/{key}__{layout.name}.pdf"}
        for index, (key, invoice) in enumerate(CATALOGUE.items())
        for layout in layouts_for(index)
    ]


def test_a_ten_document_sample_covers_every_layout_and_language():
    sample = select_sample(manifest_like_entries(), 10)
    assert len(sample) == 10
    assert {entry["layout"] for entry in sample} == {layout.name for layout in LAYOUTS}
    assert {entry["language"] for entry in sample} == {"nl", "fr", "en"}


def test_the_sample_takes_every_hard_case_first():
    sample = select_sample(manifest_like_entries(), 10)
    assert set(PRIORITY) <= {entry["key"] for entry in sample}


def test_the_sample_takes_one_layout_per_invoice():
    sample = select_sample(manifest_like_entries(), 10)
    assert len({entry["key"] for entry in sample}) == len(sample)


def test_the_sample_is_deterministic():
    entries = manifest_like_entries()
    assert select_sample(entries, 10) == select_sample(entries, 10)


def test_every_priority_key_exists_in_the_catalogue():
    assert set(PRIORITY) <= set(CATALOGUE)


def test_a_sample_larger_than_the_catalogue_returns_everything_once():
    sample = select_sample(manifest_like_entries(), 1_000)
    assert len(sample) == len(CATALOGUE)
    assert Counter(entry["key"] for entry in sample).most_common(1)[0][1] == 1


# --- the runner ---------------------------------------------------------------


class ReadsTheDocument:
    """A fake client that returns the perfect extraction for whichever invoice's
    PDF it was sent, so a multi-document run can be exercised offline. The
    stand-in PDFs carry their catalogue key, which is how it tells them apart."""

    def __init__(self, misread: set[str] = frozenset(), fail_on: set[str] = frozenset()):
        self.misread = misread
        self.fail_on = fail_on
        self.calls: list[FakeCall] = []
        self.messages = self

    def stream(self, **kwargs) -> FakeStream:
        self.calls.append(FakeCall(kwargs))
        document = next(b for b in kwargs["messages"][0]["content"] if b["type"] == "document")
        key = base64.b64decode(document["source"]["data"]).decode().split("\n")[1]
        if key in self.fail_on:
            raise ConnectionError(f"network dropped while reading {key}")
        extraction = perfect_extraction(CATALOGUE[key])
        if key in self.misread:
            extraction = deepcopy(extraction)
            extraction.invoice_number = "MISREAD"
        return FakeStream(FakeResponse.carrying(extraction, FakeUsage(), "end_turn"))


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A real manifest and ground truth, with stand-in PDFs naming their invoice."""
    root = tmp_path / "corpus"
    documents = build(root, render_pdfs=False)
    for document in documents:
        document.pdf.parent.mkdir(parents=True, exist_ok=True)
        document.pdf.write_bytes(f"%PDF-1.4\n{document.key}\n".encode())
    return root


CONFIG = EvalConfig(
    arm="vision", model="claude-opus-5", extract_effort="low", repair_effort="high",
    allow_repair=True, sample_size=4,
)


@pytest.mark.artefacts
@requires_artefacts
def test_a_run_scores_every_sampled_document(corpus: Path, tmp_path: Path):
    client = ReadsTheDocument()
    summary = evaluate(corpus, tmp_path / "run", CONFIG, budget=Budget(), client=client)

    assert summary["documents"] == 4
    assert summary["field_accuracy"] == pytest.approx(1.0, abs=0.05)
    assert summary["valid_first_attempt"] is not None
    assert (tmp_path / "run" / "summary.json").exists()
    assert len((tmp_path / "run" / "results.jsonl").read_text().splitlines()) == 4


@pytest.mark.artefacts
@requires_artefacts
def test_a_resumed_run_does_not_pay_twice(corpus: Path, tmp_path: Path):
    """The guarantee that matters when every request costs money."""
    out = tmp_path / "run"
    evaluate(corpus, out, CONFIG, budget=Budget(), client=ReadsTheDocument())

    second = ReadsTheDocument()
    summary = evaluate(corpus, out, CONFIG, budget=Budget(), client=second)
    assert second.calls == [], "a completed run must not make a single further request"
    assert summary["documents"] == 4


@pytest.mark.artefacts
@requires_artefacts
def test_a_transient_failure_is_retried_on_resume_not_scored(corpus: Path, tmp_path: Path):
    out = tmp_path / "run"
    first_key = select_sample(json.loads((corpus / "manifest.json").read_text())["entries"], 4)
    flaky = first_key[0]["key"]

    evaluate(corpus, out, CONFIG, budget=Budget(), client=ReadsTheDocument(fail_on={flaky}))
    assert (out / "errors.jsonl").exists()
    assert len((out / "results.jsonl").read_text().splitlines()) == 3

    retry = ReadsTheDocument()
    summary = evaluate(corpus, out, CONFIG, budget=Budget(), client=retry)
    assert summary["documents"] == 4
    assert len(retry.calls) == 1, "only the failed document is retried"


@pytest.mark.artefacts
@requires_artefacts
def test_hitting_the_ceiling_stops_the_run_and_keeps_what_was_paid_for(
    corpus: Path, tmp_path: Path
):
    # FakeUsage costs $0.0475 a request, so this ceiling stops after the second.
    budget = Budget(limit_usd=Decimal("0.06"))
    summary = evaluate(corpus, tmp_path / "run", CONFIG, budget=budget,
                       client=ReadsTheDocument())

    assert summary["stopped"] is not None
    assert "ceiling" in summary["stopped"]
    assert 0 < summary["documents"] < 4


@pytest.mark.artefacts
@requires_artefacts
def test_resuming_with_a_different_configuration_is_refused(corpus: Path, tmp_path: Path):
    """Two configurations in one set of numbers would describe neither."""
    out = tmp_path / "run"
    evaluate(corpus, out, CONFIG, budget=Budget(), client=ReadsTheDocument())

    changed = EvalConfig(**{**CONFIG.__dict__, "extract_effort": "medium"})
    with pytest.raises(ConfigMismatch):
        evaluate(corpus, out, changed, budget=Budget(), client=ReadsTheDocument())


@pytest.mark.artefacts
@requires_artefacts
def test_a_misread_field_shows_up_in_the_per_field_accuracy(corpus: Path, tmp_path: Path):
    entries = json.loads((corpus / "manifest.json").read_text())["entries"]
    target = select_sample(entries, 4)[0]["key"]

    summary = evaluate(corpus, tmp_path / "run", CONFIG, budget=Budget(),
                       client=ReadsTheDocument(misread={target}))
    bt1 = summary["accuracy_by_field"]["BT-1"]
    assert bt1["correct"] == bt1["total"] - 1


@pytest.mark.artefacts
@requires_artefacts
def test_the_run_records_the_token_meters_the_cost_analysis_needs(corpus: Path, tmp_path: Path):
    summary = evaluate(corpus, tmp_path / "run", CONFIG, budget=Budget(),
                       client=ReadsTheDocument())
    assert set(summary["tokens"]) == {
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"
    }
    assert summary["cost_cents_median"] > 0
