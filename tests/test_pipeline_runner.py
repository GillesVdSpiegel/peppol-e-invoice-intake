"""The pipeline end to end, driven by a fake client. No network, no spend."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import requires_artefacts
from fake_anthropic import FakeAnthropic, FakeUsage
from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.pipeline import Arm, Budget, BudgetExceeded, run
from peppol_e_invoice_intake.pipeline.extraction import ExtractionError, extract
from peppol_e_invoice_intake.pipeline.repair import build_repair_message, describe
from perfect_extraction import perfect_extraction

pytestmark = [pytest.mark.artefacts, requires_artefacts]

KEY = "standard-single-rate"


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    """A file standing in for an invoice PDF. The fake client never reads it."""
    document = tmp_path / f"{KEY}.pdf"
    document.write_bytes(b"%PDF-1.4\nnot really a pdf\n")
    return document


def budget() -> Budget:
    return Budget(limit_usd=Decimal("5.00"))


def broken_extraction(**changes):
    extraction = deepcopy(perfect_extraction(CATALOGUE[KEY]))
    for attribute, value in changes.items():
        setattr(extraction, attribute, value)
    return extraction


# --- the happy path -----------------------------------------------------------


def test_a_clean_extraction_validates_on_the_first_attempt(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.valid
    assert result.valid_first_attempt
    assert not result.repair_attempted
    assert client.call_count == 1, "a valid document must not trigger a repair"
    assert result.xml.startswith(b"<?xml")
    assert result.reconciles
    assert result.needs_human == []


def test_cost_and_latency_are_recorded_for_every_document(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(
        perfect_extraction(CATALOGUE[KEY]),
        usage=FakeUsage(input_tokens=3_000, output_tokens=2_000),
    )
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    # 3000 in at $5/MTok + 2000 out at $25/MTok = $0.065
    assert result.usd == Decimal("0.065")
    assert result.summary()["usd_cents"] == pytest.approx(6.5)
    assert result.latency_ms >= 0


# --- the repair attempt -------------------------------------------------------


def test_an_invalid_document_gets_exactly_one_repair_attempt(pdf: Path, tmp_path: Path):
    """The cap is the point: an unbounded loop converges on something that
    validates, which is not the same as something that is right."""
    still_broken = broken_extraction(buyer_reference=None, order_reference=None)
    client = FakeAnthropic(still_broken, still_broken)

    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.repair_attempted
    assert client.call_count == 2, "exactly one repair, never a loop"
    assert not result.valid


def test_a_successful_repair_is_kept_and_reported_as_repaired(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(
        broken_extraction(buyer_reference=None, order_reference=None),
        perfect_extraction(CATALOGUE[KEY]),
    )
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.repair_attempted
    assert result.valid
    assert not result.valid_first_attempt
    assert result.repaired_successfully


def test_a_repair_that_does_not_help_is_discarded(pdf: Path, tmp_path: Path):
    """A second reading that validates no better is not an improvement, and
    keeping it would hide the first reading behind a rewrite of unknown quality."""
    first = broken_extraction(buyer_reference=None, order_reference=None)
    worse = broken_extraction(buyer_reference=None, order_reference=None, invoice_number=None)
    client = FakeAnthropic(first, worse)

    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.extraction.invoice_number == first.invoice_number
    assert result.first_validation is result.validation


def test_repair_can_be_switched_off(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(broken_extraction(buyer_reference=None, order_reference=None))
    result = run(pdf, budget=budget(), client=client, allow_repair=False, workdir=tmp_path / "o")

    assert not result.repair_attempted
    assert client.call_count == 1


def test_the_repair_request_re_sends_the_document(pdf: Path, tmp_path: Path):
    """The model cannot correct a misreading it is not allowed to look at again."""
    broken = broken_extraction(buyer_reference=None, order_reference=None)
    client = FakeAnthropic(broken, perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert client.calls[1].has_pdf


def test_the_repair_request_carries_described_problems_not_raw_validator_output(
    pdf: Path, tmp_path: Path
):
    broken = broken_extraction(buyer_reference=None, order_reference=None)
    client = FakeAnthropic(broken, perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    message = client.calls[1].user_text
    assert "PEPPOL-EN16931-R003" in message
    assert "buyer reference or purchase order reference" in message
    assert "svrl" not in message.lower()
    assert "failed-assert" not in message


def test_the_repair_prompt_tells_the_model_not_to_invent_values(pdf: Path, tmp_path: Path):
    broken = broken_extraction(buyer_reference=None, order_reference=None)
    client = FakeAnthropic(broken, perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    system = client.calls[1].system_text
    assert "Do not invent a value to satisfy a rule" in system


def test_the_repair_prompt_points_away_from_the_totals(pdf: Path, tmp_path: Path):
    """Totals are computed, so a total-shaped failure is really a line failure."""
    broken = broken_extraction(buyer_reference=None, order_reference=None)
    client = FakeAnthropic(broken, perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    system = client.calls[1].system_text
    assert "Do not change a printed total" in system
    assert "the printed totals are the reference" in system


def test_the_previous_reading_is_included_in_the_repair_request(pdf: Path, tmp_path: Path):
    broken = broken_extraction(buyer_reference=None, order_reference=None)
    client = FakeAnthropic(broken, perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert CATALOGUE[KEY].number in client.calls[1].user_text


# --- failures -----------------------------------------------------------------


def test_a_truncated_extraction_is_an_error_not_a_partial_invoice(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]), stop_reason="max_tokens")
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.error is not None
    assert "truncated" in result.error
    assert result.invoice is None


def test_a_refusal_is_reported_rather_than_treated_as_empty(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]), stop_reason="refusal")
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")
    assert result.error is not None and "declined" in result.error


def test_an_unmappable_document_reports_why(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(broken_extraction(issue_date=None))
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.invoice is None
    assert result.error == "the document could not be mapped to an invoice"
    assert any(p.field == "BT-2" for p in result.problems)


def test_an_exhausted_budget_stops_before_spending_more(pdf: Path):
    spent = Budget(limit_usd=Decimal("0"))
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))

    with pytest.raises(BudgetExceeded):
        extract(pdf, budget=spent, client=client)
    assert client.call_count == 0, "the ceiling must be checked before the request"


# --- the arms -----------------------------------------------------------------


def test_the_vision_arm_sends_the_pdf_itself(pdf: Path):
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    extract(pdf, arm=Arm.VISION, budget=budget(), client=client)

    call = client.calls[0]
    assert call.has_pdf
    assert "attached as a PDF" in call.user_text


def test_the_text_arm_sends_no_pdf(tmp_path: Path):
    from peppol_e_invoice_intake.corpus.render import PdfRenderer

    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with PdfRenderer() as renderer:
            document = renderer.render(CATALOGUE[KEY], tmp_path / "real.pdf", "classic")
    except playwright.Error as exc:  # pragma: no cover - depends on local install
        pytest.skip(f"Chromium not installed: {exc}")

    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    extract(document, arm=Arm.TEXT, budget=budget(), client=client)

    call = client.calls[0]
    assert not call.has_pdf
    assert CATALOGUE[KEY].number in call.user_text
    assert "text layer" in call.user_text


def test_the_text_arm_reports_a_document_it_cannot_read(pdf: Path):
    """An intake pipeline is handed corrupt files as a matter of course. That is a
    reported unreadable document, not a pypdf traceback."""
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    with pytest.raises(ExtractionError, match="could not be read as a PDF"):
        extract(pdf, arm=Arm.TEXT, budget=budget(), client=client)


def test_the_text_arm_refuses_a_pdf_with_no_text_layer(tmp_path: Path, monkeypatch):
    """A scanned invoice parses fine but carries no text. Saying so beats
    hallucinating around an empty string."""
    import peppol_e_invoice_intake.pipeline.extraction as extract_module

    document = tmp_path / "scan.pdf"
    document.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(extract_module, "pdf_text", lambda _document: "")

    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    with pytest.raises(ExtractionError, match="no text layer"):
        extract(document, arm=Arm.TEXT, budget=budget(), client=client)


def test_the_system_prompt_is_cached_between_documents(pdf: Path):
    """It is identical for every document in a run, so paying for it once matters."""
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    extract(pdf, budget=budget(), client=client)

    system = client.calls[0].kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


# --- finding descriptions -----------------------------------------------------


def test_findings_are_described_with_their_layer_and_rule():
    from peppol_e_invoice_intake.validation import Layer, Severity, ValidationFinding

    finding = ValidationFinding(
        layer=Layer.PEPPOL,
        severity=Severity.FATAL,
        message="Buyer reference MUST be provided",
        rule_id="PEPPOL-EN16931-R003",
        location="/*:Invoice[1]",
    )
    text = describe(finding)
    assert "Peppol BIS 3.0" in text
    assert "PEPPOL-EN16931-R003" in text
    assert "Buyer reference MUST be provided" in text


def test_the_repair_message_survives_having_no_mapping_problems():
    from peppol_e_invoice_intake.validation import (
        Layer,
        Severity,
        ValidationFinding,
        ValidationResult,
    )

    validation = ValidationResult(
        document="x.xml",
        backend="saxon",
        findings=(
            ValidationFinding(
                layer=Layer.EN16931,
                severity=Severity.FATAL,
                message="An Invoice shall have an Invoice number",
                rule_id="BR-02",
            ),
        ),
    )
    message = build_repair_message(perfect_extraction(CATALOGUE[KEY]), validation, [])
    assert "BR-02" in message
    assert "corrected extraction" in message


# --- reconciliation as a repair trigger ---------------------------------------


def misread_quantity():
    """A reading that validates perfectly and is wrong: the quantity on line 1 was
    misread, so the computed totals disagree with the ones printed on the page."""
    extraction = deepcopy(perfect_extraction(CATALOGUE[KEY]))
    extraction.lines[0].quantity = "1"  # printed as 10
    return extraction


def test_a_valid_but_unreconciled_reading_still_gets_a_repair(pdf: Path, tmp_path: Path):
    """The failure validation cannot see. Totals are recomputed, so a misread line
    produces a self-consistent, valid document for the wrong amount."""
    client = FakeAnthropic(misread_quantity(), perfect_extraction(CATALOGUE[KEY]))
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.valid_first_attempt, "the misreading is invisible to validation"
    assert result.first_reconciles is False
    assert result.repair_attempted
    assert result.repair_kept
    assert result.reconciles


def test_the_repair_request_explains_the_reconciliation_failure(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(misread_quantity(), perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    message = client.calls[1].user_text
    assert "do not add up to the totals printed" in message
    assert "BT-106" in message
    assert "failed validation" not in message, "it passed validation; do not say otherwise"


def test_an_unreconciled_repair_that_is_no_better_is_discarded(pdf: Path, tmp_path: Path):
    first = misread_quantity()
    client = FakeAnthropic(first, misread_quantity())
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")

    assert result.repair_attempted
    assert not result.repair_kept
    assert not result.reconciles


def test_a_clean_reading_that_reconciles_is_never_repaired(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    result = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")
    assert result.first_reconciles is True
    assert client.call_count == 1


# --- effort: cheap first pass, careful repair ---------------------------------


def test_the_first_reading_runs_at_low_effort(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")
    assert client.calls[0].kwargs["output_config"]["effort"] == "low"


def test_the_repair_runs_at_a_higher_effort_than_the_first_reading(pdf: Path, tmp_path: Path):
    """Run cheap, and pay for a careful second look only where a check failed."""
    client = FakeAnthropic(misread_quantity(), perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, workdir=tmp_path / "out")
    assert client.calls[0].kwargs["output_config"]["effort"] == "low"
    assert client.calls[1].kwargs["output_config"]["effort"] == "high"


def test_effort_can_be_left_to_the_model_default(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(perfect_extraction(CATALOGUE[KEY]))
    run(pdf, budget=budget(), client=client, extract_effort=None, workdir=tmp_path / "out")
    assert "effort" not in client.calls[0].kwargs["output_config"]


def test_token_meters_are_recorded_per_document(pdf: Path, tmp_path: Path):
    client = FakeAnthropic(
        misread_quantity(),
        perfect_extraction(CATALOGUE[KEY]),
        usage=FakeUsage(input_tokens=3_000, output_tokens=1_000, cache_read_input_tokens=700),
    )
    summary = run(pdf, budget=budget(), client=client, workdir=tmp_path / "out").summary()
    assert summary["requests"] == 2
    assert summary["input_tokens"] == 6_000
    assert summary["output_tokens"] == 2_000
    assert summary["cache_read_tokens"] == 1_400
