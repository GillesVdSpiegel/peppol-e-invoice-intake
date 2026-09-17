"""Generated UBL must pass the Phase 1 validator, and ground truth must match it.

This is the gate that makes the corpus trustworthy. "Start from known-good UBL"
only means something if every generated document is actually checked against the
official rule sets rather than assumed correct because the emitter looked right.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import requires_artefacts
from peppol_e_invoice_intake.corpus.fields import (
    ALL_FIELDS,
    Kind,
    extract_ground_truth,
    values_match,
)
from peppol_e_invoice_intake.corpus.model import Invoice, InvoiceLine, Language, VatCategory
from peppol_e_invoice_intake.corpus.ubl import to_xml
from peppol_e_invoice_intake.validation import validate
from test_corpus_model import CUSTOMER, SUPPLIER, line


def build(lines, **kwargs) -> Invoice:
    return Invoice(
        number="T-1",
        issue_date=date(2026, 3, 2),
        due_date=date(2026, 4, 1),
        supplier=SUPPLIER,
        customer=CUSTOMER,
        buyer_reference="REF-1",
        lines=lines,
        **kwargs,
    )


STANDARD = build([
    line(line_id="1", quantity=Decimal("10"), unit_price=Decimal("100.00")),
    line(line_id="2", quantity=Decimal("2"), unit_price=Decimal("250.00")),
])

MULTI_RATE = build([
    line(line_id="1", unit_price=Decimal("100.00"), vat_rate=Decimal("21")),
    line(line_id="2", unit_price=Decimal("200.00"), vat_rate=Decimal("6")),
])

WITH_DISCOUNT = build([
    line(line_id="1", quantity=Decimal("2"), unit_price=Decimal("250.00"),
         discount=Decimal("25.00")),
])

PREPAID = build([line(unit_price=Decimal("1000.00"))], prepaid_amount=Decimal("500.00"))

FULL = build(
    [
        InvoiceLine(
            line_id="1",
            name="Palletzending",
            description="Afhaling Antwerpen Noord",
            quantity=Decimal("10"),
            unit_price=Decimal("100.00"),
        )
    ],
    order_reference="BB-1",
    note="Raamovereenkomst 2026/LOG/014.",
    iban="BE68539007547034",
    payment_reference="+++260/0000/14419+++",
    payment_terms="Betaalbaar binnen 30 dagen",
    language=Language.NL,
)

CASES = {
    "standard-single-rate": STANDARD,
    "multiple-vat-rates": MULTI_RATE,
    "line-discount": WITH_DISCOUNT,
    "prepaid-amount": PREPAID,
    "all-optional-fields": FULL,
}


@pytest.mark.artefacts
@requires_artefacts
@pytest.mark.parametrize("invoice", CASES.values(), ids=list(CASES))
def test_generated_ubl_passes_validation(invoice: Invoice, tmp_path: Path):
    document = tmp_path / "generated.xml"
    document.write_bytes(to_xml(invoice))
    result = validate(document)
    assert result.is_valid, "\n".join(str(f) for f in result.blocking)


def test_ground_truth_reflects_the_emitted_totals():
    truth = extract_ground_truth(to_xml(MULTI_RATE))
    assert truth["document"]["BT-106"] == "300.00"
    assert truth["document"]["BT-110"] == "33.00"
    assert truth["document"]["BT-112"] == "333.00"
    assert truth["document"]["BT-115"] == "333.00"
    assert len(truth["vat_breakdown"]) == 2


def test_ground_truth_omits_absent_fields_rather_than_recording_null():
    """Scoring must be able to tell 'not in the document' from 'model missed it'."""
    truth = extract_ground_truth(to_xml(STANDARD))
    assert "BT-13" not in truth["document"]
    assert "BT-22" not in truth["document"]
    assert "BT-136" not in truth["lines"][0]


def test_ground_truth_captures_line_level_discounts():
    truth = extract_ground_truth(to_xml(WITH_DISCOUNT))
    assert truth["lines"][0]["BT-136"] == "25.00"
    assert truth["lines"][0]["BT-131"] == "475.00"


def test_every_ground_truth_key_is_a_known_business_term():
    truth = extract_ground_truth(to_xml(FULL))
    keys = set(truth["document"])
    for group in (*truth["lines"], *truth["vat_breakdown"]):
        keys |= set(group)
    assert keys <= set(ALL_FIELDS)


def test_amount_comparison_ignores_formatting():
    assert values_match(Kind.AMOUNT, "1500.00", "1500.0")
    assert values_match(Kind.AMOUNT, "1500.00", "1500")
    assert not values_match(Kind.AMOUNT, "1500.00", "1500.01")


def test_text_comparison_normalises_whitespace_but_not_case():
    assert values_match(Kind.TEXT, "Havenkantoor  Logistiek\nBV", "Havenkantoor Logistiek BV")
    assert not values_match(Kind.TEXT, "Havenkantoor", "havenkantoor")


def test_code_comparison_is_case_insensitive():
    assert values_match(Kind.CODE, "be", "BE")


def test_a_missing_value_never_matches_a_present_one():
    assert not values_match(Kind.TEXT, None, "x")
    assert not values_match(Kind.AMOUNT, "1.00", None)
    assert values_match(Kind.TEXT, None, None)


@pytest.mark.artefacts
@requires_artefacts
def test_reverse_charge_invoice_validates_with_an_exemption_reason(tmp_path: Path):
    invoice = build(
        [line(vat_category=VatCategory.REVERSE_CHARGE, vat_rate=Decimal("0"))],
        exemption_reason="BTW verlegd - artikel 20 KB nr. 1",
    )
    document = tmp_path / "reverse-charge.xml"
    document.write_bytes(to_xml(invoice))
    result = validate(document)
    assert result.is_valid, "\n".join(str(f) for f in result.blocking)
