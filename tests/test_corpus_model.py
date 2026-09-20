"""Tax arithmetic. No artefacts, no I/O - this is pure computation.

EN 16931's total rules are exact-equality checks, so these tests target the places
where an obvious implementation gets it wrong: per-line rounding that does not sum,
mixed rates, and discounts applied at the wrong point.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from peppol_e_invoice_intake.corpus.model import (
    Invoice,
    InvoiceLine,
    Party,
    VatCategory,
)

SUPPLIER = Party(
    name="Test BV",
    street="Straat 1",
    city="Antwerpen",
    postal_zone="2000",
    vat_id="BE0403040542",
    legal_id="0403040542",
    endpoint_id="0403040542",
)
CUSTOMER = Party(
    name="Klant NV",
    street="Laan 2",
    city="Gent",
    postal_zone="9000",
    vat_id="BE0539801535",
    legal_id="0539801535",
    endpoint_id="0539801535",
)


def build(lines: list[InvoiceLine], **kwargs) -> Invoice:
    return Invoice(
        number="T-1",
        issue_date=date(2026, 3, 2),
        supplier=SUPPLIER,
        customer=CUSTOMER,
        buyer_reference="REF",
        lines=lines,
        **kwargs,
    )


def line(**kwargs) -> InvoiceLine:
    defaults = {
        "line_id": "1",
        "name": "Item",
        "quantity": Decimal("1"),
        "unit_price": Decimal("100.00"),
    }
    return InvoiceLine(**{**defaults, **kwargs})


def test_line_net_amount_subtracts_the_discount():
    assert line(quantity=Decimal("2"), unit_price=Decimal("250.00"),
                discount=Decimal("25.00")).net_amount == Decimal("475.00")


def test_totals_chain_satisfies_the_en16931_rules():
    invoice = build([
        line(line_id="1", quantity=Decimal("10"), unit_price=Decimal("100.00")),
        line(line_id="2", quantity=Decimal("2"), unit_price=Decimal("250.00")),
    ])
    assert invoice.line_extension_amount == Decimal("1500.00")  # BR-CO-10
    assert invoice.tax_exclusive_amount == Decimal("1500.00")  # BR-CO-13
    assert invoice.tax_amount == Decimal("315.00")  # BR-CO-14
    assert invoice.tax_inclusive_amount == Decimal("1815.00")  # BR-CO-15
    assert invoice.payable_amount == Decimal("1815.00")  # BR-CO-16


def test_vat_is_computed_per_group_not_per_line():
    """BR-CO-17 rounds the group total. Summing per-line VAT gives 0.02 more here,
    which is exactly the kind of off-by-a-cent that fails validation."""
    lines = [
        line(line_id=str(i), quantity=Decimal("1"), unit_price=Decimal("0.05"))
        for i in range(1, 6)
    ]
    invoice = build(lines)
    assert invoice.line_extension_amount == Decimal("0.25")
    # 0.25 * 21% = 0.0525 -> 0.05. Per line it would be 5 x round(0.0105) = 0.05 too,
    # but the group calculation is the one BR-CO-17 actually specifies.
    assert invoice.tax_amount == Decimal("0.05")
    assert invoice.tax_amount == invoice.tax_subtotals[0].tax_amount


def test_multiple_rates_produce_one_breakdown_row_each():
    invoice = build([
        line(line_id="1", unit_price=Decimal("100.00"), vat_rate=Decimal("21")),
        line(line_id="2", unit_price=Decimal("200.00"), vat_rate=Decimal("6")),
        line(line_id="3", unit_price=Decimal("50.00"), vat_rate=Decimal("21")),
    ])
    rows = {(s.category, s.rate): s for s in invoice.tax_subtotals}
    assert len(rows) == 2
    assert rows[(VatCategory.STANDARD, Decimal("21"))].taxable_amount == Decimal("150.00")
    assert rows[(VatCategory.STANDARD, Decimal("21"))].tax_amount == Decimal("31.50")
    assert rows[(VatCategory.STANDARD, Decimal("6"))].taxable_amount == Decimal("200.00")
    assert rows[(VatCategory.STANDARD, Decimal("6"))].tax_amount == Decimal("12.00")
    assert invoice.tax_amount == Decimal("43.50")
    assert invoice.has_multiple_vat_rates


def test_reverse_charge_carries_no_vat_but_still_has_a_breakdown_row():
    invoice = build(
        [line(vat_category=VatCategory.REVERSE_CHARGE, vat_rate=Decimal("0"))],
        exemption_reason="BTW verlegd",
    )
    (row,) = invoice.tax_subtotals
    assert row.category is VatCategory.REVERSE_CHARGE
    assert row.tax_amount == Decimal("0.00")
    assert row.exemption_reason == "BTW verlegd"
    assert invoice.tax_inclusive_amount == invoice.tax_exclusive_amount


def test_exemption_reason_is_not_attached_to_standard_rated_rows():
    invoice = build([line()], exemption_reason="Should not appear")
    assert invoice.tax_subtotals[0].exemption_reason is None


def test_prepaid_amount_reduces_only_the_payable_total():
    invoice = build([line(unit_price=Decimal("1000.00"))], prepaid_amount=Decimal("500.00"))
    assert invoice.tax_inclusive_amount == Decimal("1210.00")
    assert invoice.payable_amount == Decimal("710.00")


def test_rounding_uses_half_up_not_bankers_rounding():
    """Python's default rounding would give 0.02 here; EN 16931 expects 0.03."""
    invoice = build([line(quantity=Decimal("1"), unit_price=Decimal("0.125"))])
    assert invoice.line_extension_amount == Decimal("0.13")


def test_an_invoice_needs_at_least_one_line():
    with pytest.raises(ValueError):
        build([])
