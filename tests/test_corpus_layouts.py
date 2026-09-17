"""Every layout must actually print every figure it claims to.

This exists because of a real bug. The "ledger" layout used CSS generated content
for its dotted leaders; the string overflowed the row and pushed three total
amounts off the page. The HTML was correct, the PDF rendered without error, and
the document was quietly missing data.

That failure mode is worse than a crash. Ground truth is read from the UBL, so a
figure clipped out of the PDF becomes a label asserting something the document
does not show - and phase 4 would score the pipeline down for correctly failing
to find it. These tests render each layout to PDF and read the text back.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from peppol_e_invoice_intake.corpus.i18n import format_amount
from peppol_e_invoice_intake.corpus.model import Invoice, Language, VatCategory
from peppol_e_invoice_intake.corpus.render import LAYOUTS, PdfRenderer, render_html
from test_corpus_model import CUSTOMER, SUPPLIER, line

pytestmark = pytest.mark.pdf


def sample(language: Language = Language.NL, **kwargs) -> Invoice:
    params = {
        "number": "2026-0001",
        "issue_date": date(2026, 3, 2),
        "due_date": date(2026, 4, 1),
        "supplier": SUPPLIER,
        "customer": CUSTOMER,
        "buyer_reference": "PO-2026-0442",
        "order_reference": "BB-88213",
        "iban": "BE68539007547034",
        "payment_reference": "+++260/0000/14419+++",
        "payment_terms": "binnen 30 dagen",
        "language": language,
        "lines": [
            line(line_id="1", quantity=Decimal("10"), unit_price=Decimal("100.00")),
            line(line_id="2", quantity=Decimal("2"), unit_price=Decimal("250.00"),
                 discount=Decimal("25.00")),
            line(line_id="3", quantity=Decimal("1"), unit_price=Decimal("45.50"),
                 vat_rate=Decimal("6")),
        ],
    }
    return Invoice(**{**params, **kwargs})


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict[str, str]:
    """Render every layout once and return the extracted text of each."""
    playwright = pytest.importorskip("playwright.sync_api")
    directory = tmp_path_factory.mktemp("layouts")
    invoice = sample()
    texts = {}
    try:
        with PdfRenderer() as renderer:
            for layout in LAYOUTS:
                path = renderer.render(invoice, directory / f"{layout.name}.pdf", layout)
                texts[layout.name] = pdf_text(path)
    except playwright.Error as exc:  # pragma: no cover - depends on local install
        pytest.skip(f"Chromium not installed: {exc}")
    return texts


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.name)
def test_every_total_is_visible_in_the_pdf(layout, rendered: dict[str, str]):
    invoice = sample()
    text = rendered[layout.name].replace("−", "-")
    for name, amount in (
        ("tax exclusive", invoice.tax_exclusive_amount),
        ("total VAT", invoice.tax_amount),
        ("tax inclusive", invoice.tax_inclusive_amount),
        ("payable", invoice.payable_amount),
    ):
        printed = format_amount(amount, invoice.language)
        assert printed in text, f"{layout.name}: {name} ({printed}) is missing from the PDF"


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.name)
def test_every_line_amount_is_visible_in_the_pdf(layout, rendered: dict[str, str]):
    invoice = sample()
    text = rendered[layout.name]
    for invoice_line in invoice.lines:
        printed = format_amount(invoice_line.net_amount, invoice.language)
        assert printed in text, (
            f"{layout.name}: line {invoice_line.line_id} amount ({printed}) is missing"
        )


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.name)
def test_identifying_fields_are_visible_in_the_pdf(layout, rendered: dict[str, str]):
    invoice = sample()
    text = rendered[layout.name]
    for label, value in (
        ("invoice number", invoice.number),
        ("seller name", invoice.supplier.name),
        ("buyer name", invoice.customer.name),
        ("seller VAT", invoice.supplier.vat_id),
        ("buyer VAT", invoice.customer.vat_id),
        ("IBAN", invoice.iban),
        ("buyer reference", invoice.buyer_reference),
    ):
        assert value in text, f"{layout.name}: {label} ({value}) is missing from the PDF"


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.name)
def test_vat_breakdown_figures_are_visible_in_the_pdf(layout, rendered: dict[str, str]):
    invoice = sample()
    text = rendered[layout.name]
    for subtotal in invoice.tax_subtotals:
        for value in (subtotal.taxable_amount, subtotal.tax_amount):
            printed = format_amount(value, invoice.language)
            assert printed in text, (
                f"{layout.name}: VAT breakdown figure {printed} is missing from the PDF"
            )


def test_layouts_do_not_all_use_the_same_wording():
    """The variation is the point; if every layout resolved to the same labels the
    corpus would reward memorising strings rather than reading the document."""
    wordings = {
        layout.name: render_html(sample(), layout) for layout in LAYOUTS
    }
    taxable_labels = {
        name for name, html in wordings.items()
        if "Belastbare basis" in html
    }
    assert 0 < len(taxable_labels) < len(LAYOUTS), (
        "expected the taxable-base label to differ across layouts"
    )


def test_layout_names_and_templates_are_unique():
    assert len({layout.name for layout in LAYOUTS}) == len(LAYOUTS)
    assert len({layout.template for layout in LAYOUTS}) == len(LAYOUTS)


@pytest.mark.parametrize("language", list(Language))
def test_every_layout_renders_in_every_language(language: Language):
    for layout in LAYOUTS:
        html = render_html(sample(language), layout)
        assert "2026-0001" in html


def test_exempt_categories_render_in_every_layout():
    invoice = sample(
        lines=[line(vat_category=VatCategory.INTRA_COMMUNITY, vat_rate=Decimal("0"))],
        exemption_reason="Intracommunautaire levering, artikel 39bis",
    )
    for layout in LAYOUTS:
        html = render_html(invoice, layout)
        assert "Intracommunautaire levering" in html
