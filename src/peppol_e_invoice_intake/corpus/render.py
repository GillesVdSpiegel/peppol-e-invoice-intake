"""Render an `Invoice` to HTML, and HTML to PDF.

HTML plus headless Chromium was chosen over a drawing library so that a new visual
layout costs a template rather than a page-layout program: multi-page line tables,
repeating headers and page breaks come from CSS instead of manual cursor
arithmetic. The cost is a ~150MB browser download, which corpus generation needs
and the validation tests do not.

The browser is expensive to start and cheap to reuse, so `PdfRenderer` keeps one
Chromium alive for a whole batch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import TracebackType

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .i18n import (
    VAT_CATEGORY_NOTE,
    format_amount,
    format_date,
    format_quantity,
    format_rate,
    labels,
)
from .model import Invoice, Language, VatCategory

TEMPLATE_DIR = Path(__file__).parent / "templates"

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


@dataclass(frozen=True)
class Layout:
    """A visual style. Adding one means adding a template, not changing code.

    `label_variants` gives each layout its own wording for shared business terms.
    Real suppliers do not agree on any of it, and without the variation a pipeline
    could memorise one string per field and score better than it deserves.
    """

    name: str
    template: str
    description: str
    label_variants: Mapping[str, str] = field(default_factory=dict)


LAYOUTS: tuple[Layout, ...] = (
    Layout(
        "classic",
        "classic.html.j2",
        "Conventional Belgian invoice: letterhead left, totals stacked right",
    ),
    Layout(
        "modern",
        "modern.html.j2",
        "Coloured header band, amount due stated up front, VAT summary as prose",
        {"taxable": "bedrag-excl", "payable": "totaal-te-betalen", "description": "artikel"},
    ),
    Layout(
        "compact",
        "compact.html.j2",
        "Dense single column, hairline rules, totals and VAT side by side at the foot",
        {"taxable": "maatstaf", "quantity": "hoeveelheid", "line_total": "bedrag"},
    ),
    Layout(
        "ledger",
        "ledger.html.j2",
        "Accounting style, tabular figures, amount column first, VAT in a footer band",
        {
            "taxable": "maatstaf-van-heffing",
            "payable": "te-voldoen",
            "unit_price": "stukprijs",
            "vat_breakdown": "btw-detail",
        },
    ),
    Layout(
        "letterhead",
        "letterhead.html.j2",
        "Formal letter, recipient block placed for a window envelope, boxed totals",
        {"description": "prestatie", "buyer_reference": "klantreferentie",
         "unit_price": "prijs-per-eenheid"},
    ),
    Layout(
        "minimal",
        "minimal.html.j2",
        "Almost plain text: no rules, no colour, weak column structure",
        {"taxable": "bedrag-excl", "payable": "totaal-te-betalen",
         "buyer_reference": "referentie", "line_total": "bedrag"},
    ),
)

LAYOUTS_BY_NAME = {layout.name: layout for layout in LAYOUTS}


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(invoice: Invoice, layout: Layout | str = "classic") -> str:
    """Render to HTML. Language comes from the invoice, not a separate argument,
    so a document cannot be labelled in one language and rendered in another."""
    resolved = LAYOUTS_BY_NAME[layout] if isinstance(layout, str) else layout
    language: Language = invoice.language
    template = _environment().get_template(resolved.template)

    return template.render(
        invoice=invoice,
        L=labels(language, dict(resolved.label_variants)),
        layout=resolved,
        currency_symbol=CURRENCY_SYMBOLS.get(invoice.currency, invoice.currency),
        d=lambda value: format_date(value, language),
        m=lambda value: format_amount(Decimal(value), language),
        q=lambda value: format_quantity(Decimal(value), language),
        r=lambda value: format_rate(Decimal(value), language),
        vat_note=lambda category: VAT_CATEGORY_NOTE[language].get(
            VatCategory(category), VatCategory(category).value
        ),
    )


class PdfRenderer:
    """Keeps one headless Chromium alive across a batch of renders."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    def __enter__(self) -> PdfRenderer:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = self._playwright = None

    def html_to_pdf(self, html: str, destination: Path) -> Path:
        if self._browser is None:
            raise RuntimeError("PdfRenderer must be used as a context manager")
        destination.parent.mkdir(parents=True, exist_ok=True)
        page = self._browser.new_page()
        try:
            # No external resources are referenced, so there is nothing to wait for
            # beyond the document itself.
            page.set_content(html, wait_until="load")
            page.pdf(path=str(destination), format="A4", print_background=True)
        finally:
            page.close()
        return destination

    def render(
        self, invoice: Invoice, destination: Path, layout: Layout | str = "classic"
    ) -> Path:
        return self.html_to_pdf(render_html(invoice, layout), destination)


def render_pdf(invoice: Invoice, destination: Path, layout: Layout | str = "classic") -> Path:
    """Convenience wrapper for a single document. Prefer `PdfRenderer` for batches."""
    with PdfRenderer() as renderer:
        return renderer.render(invoice, destination, layout)
