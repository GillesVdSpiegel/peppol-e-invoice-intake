"""Mapping and reconciliation. No network, no spend.

A perfect extraction is the pipeline's upper bound, so these tests isolate the
deterministic half: if something fails here, the fault is in mapping rather than
in the reading.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import requires_artefacts
from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.corpus.model import VatCategory
from peppol_e_invoice_intake.corpus.ubl import to_xml
from peppol_e_invoice_intake.pipeline.mapping import ProblemKind, map_to_invoice
from peppol_e_invoice_intake.validation import validate
from perfect_extraction import perfect_extraction

#: The Swiss buyer carries neither a VAT nor a registration number, so no Peppol
#: electronic address can be derived from anything printed on the page. The
#: pipeline is supposed to say so rather than invent one, which means this
#: document cannot validate - by design.
UNDERIVABLE_ENDPOINT = {"export-outside-eu"}
MAPPABLE = [key for key in CATALOGUE if key not in UNDERIVABLE_ENDPOINT]


@pytest.mark.artefacts
@requires_artefacts
@pytest.mark.parametrize("key", MAPPABLE, ids=MAPPABLE)
def test_a_perfect_extraction_maps_to_a_valid_invoice(key: str, tmp_path: Path):
    original = CATALOGUE[key]
    result = map_to_invoice(perfect_extraction(original), language=original.language)

    assert result.invoice is not None
    document = tmp_path / f"{key}.xml"
    document.write_bytes(to_xml(result.invoice))
    validation = validate(document)
    assert validation.is_valid, "\n".join(str(f) for f in validation.blocking)


@pytest.mark.parametrize("key", MAPPABLE, ids=MAPPABLE)
def test_a_perfect_extraction_raises_nothing_for_a_human(key: str):
    original = CATALOGUE[key]
    result = map_to_invoice(perfect_extraction(original), language=original.language)
    assert result.blocking == [], [str(p) for p in result.blocking]


@pytest.mark.parametrize("key", MAPPABLE, ids=MAPPABLE)
def test_a_perfect_extraction_reconciles(key: str):
    """Computed totals must equal the ones the document states."""
    original = CATALOGUE[key]
    result = map_to_invoice(perfect_extraction(original), language=original.language)
    mismatches = [p for p in result.problems if p.kind is ProblemKind.RECONCILIATION]
    assert mismatches == [], [str(p) for p in mismatches]


@pytest.mark.artefacts
@requires_artefacts
def test_an_underivable_endpoint_is_reported_rather_than_invented(tmp_path: Path):
    """The honest failure. No VAT or registration number is printed, so the Peppol
    address cannot be derived - and a fabricated one would be worse than none."""
    original = CATALOGUE["export-outside-eu"]
    result = map_to_invoice(perfect_extraction(original), language=original.language)

    assert result.invoice is not None
    problem = next(
        p for p in result.blocking if "BT-34/BT-49" in p.field and "buyer" in p.field
    )
    assert problem.kind is ProblemKind.MISSING
    assert "person" in problem.detail

    document = tmp_path / "export.xml"
    document.write_bytes(to_xml(result.invoice))
    validation = validate(document)
    assert not validation.is_valid
    assert "PEPPOL-EN16931-R010" in validation.rule_ids()


def test_endpoints_are_derived_from_printed_identifiers():
    """Invoices never print a Peppol address; it has to come from something else."""
    original = CATALOGUE["standard-single-rate"]
    result = map_to_invoice(perfect_extraction(original), language=original.language)

    assert result.invoice.supplier.endpoint_scheme == "0208"
    assert result.invoice.supplier.endpoint_id == original.supplier.legal_id
    derived = [p for p in result.problems if p.kind is ProblemKind.DERIVED]
    assert len(derived) == 2
    assert all(not p.needs_human for p in derived)


def test_a_dutch_buyer_gets_the_dutch_vat_scheme():
    original = CATALOGUE["intra-community-supply"]
    result = map_to_invoice(perfect_extraction(original), language=original.language)
    assert result.invoice.customer.endpoint_scheme == "9944"


# --- degraded extractions -----------------------------------------------------


def degrade(key: str, **changes):
    extraction = deepcopy(perfect_extraction(CATALOGUE[key]))
    for attribute, value in changes.items():
        setattr(extraction, attribute, value)
    return extraction


def test_a_misread_quantity_is_caught_by_reconciliation():
    """The failure mode deterministic mapping would otherwise hide: the emitted
    invoice is internally consistent and valid, but for the wrong amount."""
    extraction = deepcopy(perfect_extraction(CATALOGUE["standard-single-rate"]))
    extraction.lines[0].quantity = "1"  # was 10

    result = map_to_invoice(extraction)
    mismatches = [p for p in result.problems if p.kind is ProblemKind.RECONCILIATION]

    assert mismatches, "a misread quantity must not pass silently"
    assert any("BT-106" in p.field for p in mismatches)
    assert any(p.needs_human for p in mismatches)


def test_reconciliation_names_the_size_of_the_disagreement():
    extraction = deepcopy(perfect_extraction(CATALOGUE["standard-single-rate"]))
    extraction.lines[0].unit_price = "110.00"  # was 100.00

    problem = next(
        p
        for p in map_to_invoice(extraction).problems
        if p.kind is ProblemKind.RECONCILIATION and p.field == "BT-106"
    )
    assert "100.00" in problem.detail  # 10 units x 10.00 more


def test_a_misread_vat_breakdown_row_is_reported():
    extraction = deepcopy(perfect_extraction(CATALOGUE["multiple-vat-rates"]))
    extraction.vat_breakdown[0].tax_amount = "999.99"

    problems = [
        p for p in map_to_invoice(extraction).problems if p.kind is ProblemKind.RECONCILIATION
    ]
    assert any("BG-23" in p.field for p in problems)


def test_an_unparsable_amount_is_reported_not_coerced():
    extraction = degrade("standard-single-rate")
    extraction.lines[0].unit_price = "1.234,56"  # not normalised to canonical form

    result = map_to_invoice(extraction)
    assert any(
        p.kind is ProblemKind.UNPARSABLE and "BT-146" in p.field for p in result.problems
    )


def test_a_line_missing_its_numbers_is_dropped_and_reported():
    extraction = degrade("line-discounts")
    extraction.lines[1].quantity = None

    result = map_to_invoice(extraction)
    assert len(result.invoice.lines) == len(CATALOGUE["line-discounts"].lines) - 1
    assert any("line dropped" in p.detail for p in result.problems)


def test_a_document_with_no_readable_line_does_not_map():
    extraction = degrade("minimal-single-line")
    extraction.lines = []

    result = map_to_invoice(extraction)
    assert result.invoice is None
    assert any(p.field == "BG-25" for p in result.problems)


def test_a_document_with_no_issue_date_does_not_map():
    result = map_to_invoice(degrade("standard-single-rate", issue_date=None))
    assert result.invoice is None
    assert any(p.field == "BT-2" for p in result.problems)


def test_a_missing_buyer_reference_is_reported_as_needing_a_person():
    """PEPPOL-EN16931-R003 requires one of BT-10 or BT-13, and neither can be
    invented from a document that does not carry them."""
    result = map_to_invoice(
        degrade("standard-single-rate", buyer_reference=None, order_reference=None)
    )
    problem = next(p for p in result.blocking if p.field == "BT-10/BT-13")
    assert "person" in problem.detail


def test_a_mistyped_enterprise_number_is_explained_before_the_rule_fires():
    extraction = degrade("standard-single-rate")
    extraction.supplier.legal_id = "0403040500"  # valid shape, wrong check digits

    problem = next(
        p for p in map_to_invoice(extraction).problems if "BT-30/BT-47" in p.field
    )
    assert "mod-97" in problem.detail
    assert "misread" in problem.detail


def test_an_unnamed_vat_category_on_a_zero_rate_needs_a_person():
    """A 0% line with no category is far more likely to be an unnamed exemption
    than a genuine zero rate, so it must not be resolved quietly."""
    extraction = degrade("reverse-charge-construction")
    for line in extraction.lines:
        line.vat_category = None

    result = map_to_invoice(extraction)
    problems = [p for p in result.blocking if "BT-151" in p.field]
    assert problems, "an unnamed category at 0% should be raised"


def test_an_unnamed_vat_category_on_a_positive_rate_is_only_informational():
    extraction = degrade("standard-single-rate")
    for line in extraction.lines:
        line.vat_category = None

    result = map_to_invoice(extraction)
    assert all(line.vat_category is VatCategory.STANDARD for line in result.invoice.lines)
    assert not [p for p in result.blocking if "BT-151" in p.field]


def test_model_uncertainties_are_carried_into_the_report():
    from peppol_e_invoice_intake.pipeline.schema import Uncertainty

    extraction = degrade("standard-single-rate")
    extraction.uncertainties = [
        Uncertainty(field="BT-9", problem="the due date was smudged", printed_text="01/0?/2026")
    ]

    result = map_to_invoice(extraction)
    problem = next(p for p in result.problems if p.kind is ProblemKind.UNCERTAINTY)
    assert problem.field == "BT-9"
    assert problem.printed == "01/0?/2026"
    assert problem.needs_human


def test_prepaid_amount_is_taken_from_the_stated_totals():
    original = CATALOGUE["partially-prepaid"]
    result = map_to_invoice(perfect_extraction(original), language=original.language)
    assert result.invoice.prepaid_amount == Decimal("4000.00")
    assert result.invoice.payable_amount == original.payable_amount
