"""Rendering. HTML assertions are cheap; the PDF test needs a browser.

The HTML tests assert the localisation gap on purpose: the PDF shows 02/03/2026
and 1.520,50 while the ground truth says 2026-03-02 and 1520.50. If a template
ever started printing ISO dates the corpus would get easier without anyone
noticing, and the published accuracy numbers would drift upward for no real reason.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from peppol_e_invoice_intake.corpus.model import Invoice, Language, VatCategory
from peppol_e_invoice_intake.corpus.render import LAYOUTS, render_html
from test_corpus_model import CUSTOMER, SUPPLIER, line


def build(language: Language = Language.NL, **kwargs) -> Invoice:
    params = {
        "number": "2026-0001",
        "issue_date": date(2026, 3, 2),
        "due_date": date(2026, 4, 1),
        "supplier": SUPPLIER,
        "customer": CUSTOMER,
        "buyer_reference": "PO-2026-0442",
        "language": language,
        "lines": [
            line(line_id="1", quantity=Decimal("10"), unit_price=Decimal("100.00")),
            line(line_id="2", quantity=Decimal("1"), unit_price=Decimal("520.50"),
                 vat_rate=Decimal("6")),
        ],
    }
    return Invoice(**{**params, **kwargs})


def test_renders_every_registered_layout():
    invoice = build()
    for layout in LAYOUTS:
        html = render_html(invoice, layout)
        assert invoice.number in html
        assert invoice.supplier.name in html
        assert invoice.customer.name in html


def test_belgian_number_formatting_appears_in_dutch_output():
    html = render_html(build(Language.NL))
    assert "1.520,50" in html
    assert "1520.50" not in html


def test_belgian_date_formatting_appears_in_dutch_output():
    html = render_html(build(Language.NL))
    assert "02/03/2026" in html
    assert "2026-03-02" not in html


def test_english_output_uses_anglo_formatting():
    html = render_html(build(Language.EN))
    assert "1,520.50" in html
    assert "2 March 2026" in html


def test_french_labels_are_used_for_french_invoices():
    html = render_html(build(Language.FR))
    assert "FACTURE" in html
    assert "Numéro de facture" in html
    assert "FACTUUR" not in html


def test_dutch_labels_are_used_for_dutch_invoices():
    html = render_html(build(Language.NL))
    assert "FACTUUR" in html
    assert "Factuurnummer" in html


def test_every_line_appears_in_the_rendered_table():
    invoice = build()
    html = render_html(invoice)
    for invoice_line in invoice.lines:
        assert invoice_line.name in html


def test_vat_breakdown_rows_are_rendered():
    html = render_html(build())
    assert "6%" in html
    assert "21%" in html


def test_exempt_categories_print_a_human_readable_note():
    invoice = build(
        lines=[line(vat_category=VatCategory.REVERSE_CHARGE, vat_rate=Decimal("0"))],
        exemption_reason="BTW verlegd - artikel 20 KB nr. 1",
    )
    html = render_html(invoice, "classic")
    assert "BTW verlegd" in html


def test_unknown_layout_is_rejected():
    with pytest.raises(KeyError):
        render_html(build(), "does-not-exist")


@pytest.mark.pdf
def test_pdf_is_produced_and_is_a_real_pdf(tmp_path: Path):
    playwright = pytest.importorskip("playwright.sync_api")
    from peppol_e_invoice_intake.corpus.render import PdfRenderer

    try:
        with PdfRenderer() as renderer:
            destination = renderer.render(build(), tmp_path / "invoice.pdf")
    except playwright.Error as exc:  # pragma: no cover - depends on local install
        pytest.skip(f"Chromium not installed: {exc}")

    assert destination.exists()
    assert destination.read_bytes().startswith(b"%PDF-")
    assert destination.stat().st_size > 5_000
