"""Ground truth may only claim what the page actually shows.

This is the check the first real evaluation run proved was missing. Two defects
had been sitting in the corpus since phase 2:

- unit prices were printed rounded to two decimals, so a ground truth of 16.665
  was scored against a page showing 16,66;
- four of six layouts printed no unit at all, so a ground truth of HUR (hours)
  was scored against a page that never said hours.

Neither was a reading error, but both counted as misses. A pipeline that
correctly refuses to invent a value would be scored down for it, which is the
opposite of what the numbers are for.

So every business term in every catalogue invoice, in every layout, must appear
on the rendered page in the form a reader would see it - 1.520,50 not 1520.50,
02/03/2026 not 2026-03-02 - unless it is listed below as implicit or derived,
with the reason it is fair to expect the pipeline to know it anyway.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import html as lxml_html

from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.corpus.fields import ALL_FIELDS, Kind, extract_ground_truth
from peppol_e_invoice_intake.corpus.i18n import (
    format_amount,
    format_date,
    format_price,
    format_quantity,
    format_rate,
    unit_label,
)
from peppol_e_invoice_intake.corpus.model import Invoice
from peppol_e_invoice_intake.corpus.render import LAYOUTS, PdfRenderer, render_html
from peppol_e_invoice_intake.corpus.ubl import to_xml

#: Fields that are fairly knowable without being printed, and why. Every entry
#: is a claim that the pipeline can get this right from what *is* on the page.
IMPLICIT: dict[str, str] = {
    "BT-3": "the page says 'invoice'; the code 380 follows from that",
    "BT-5": "the currency prints as a symbol or is implied by the Belgian context",
    "BT-34": "no paper invoice prints a Peppol address; it is derived from identifiers",
    "BT-49": "no paper invoice prints a Peppol address; it is derived from identifiers",
    "BT-40": "Belgian addresses omit the country; it follows from the address",
    "BT-55": "addresses omit the country; it follows from the address",
    "BT-126": "some layouts omit line numbers; lines are identified by position",
    "BT-151": "the category code follows from the rate and the exemption wording",
    "BT-118": "the category code follows from the rate and the exemption wording",
}


def _derivable_from_vat(truth: dict, bt: str, value: str) -> bool:
    """BT-30 / BT-47: a Belgian VAT number is 'BE' followed by the enterprise number."""
    vat = truth["document"].get("BT-31" if bt == "BT-30" else "BT-48", "")
    return vat.startswith("BE") and vat[2:] == value


def printed_forms(bt: str, value: str, invoice: Invoice, group: dict) -> list[str]:
    """How a value appears on the page. Any one of them being visible is enough.

    A unit is checked together with its quantity. On its own a French unit is
    "h", which is a substring of half the page - the first version of this audit
    checked it alone and could never fail.
    """
    language = invoice.language
    kind = ALL_FIELDS[bt].kind
    if kind is Kind.DATE:
        return [format_date(date.fromisoformat(value), language)]
    if bt == "BT-146":
        return [format_price(Decimal(value), language)]
    if kind is Kind.AMOUNT:
        return [format_amount(Decimal(value), language)]
    if bt in ("BT-152", "BT-119"):
        return [format_rate(Decimal(value), language)]
    if kind is Kind.QUANTITY:
        return [format_quantity(Decimal(value), language)]
    if bt == "BT-130":
        quantity = format_quantity(Decimal(group["BT-129"]), language)
        return [f"{quantity} {unit_label(value, language)}"]
    return [value]


def visible_text(page: str) -> str:
    text = lxml_html.fromstring(page).text_content()
    return " ".join(text.replace("−", "-").split())


def invisible_fields(invoice: Invoice, text: str) -> list[str]:
    """Every ground-truth value that the page does not show and is not implicit."""
    truth = extract_ground_truth(to_xml(invoice))
    groups = [("document", truth["document"])]
    groups += [(f"line {i + 1}", line) for i, line in enumerate(truth["lines"])]
    groups += [(f"VAT row {i + 1}", row) for i, row in enumerate(truth["vat_breakdown"])]

    missing = []
    for where, fields in groups:
        for bt, value in fields.items():
            if bt in IMPLICIT:
                continue
            if bt == "BT-130" and value == "C62":
                continue  # pieces print no unit, on real invoices too
            if bt in ("BT-30", "BT-47") and _derivable_from_vat(truth, bt, value):
                continue
            forms = printed_forms(bt, value, invoice, fields)
            if not any(" ".join(form.split()) in text for form in forms):
                missing.append(f"{where} {bt} ({ALL_FIELDS[bt].name}) = {value!r}")
    return missing


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.name)
def test_every_ground_truth_value_is_visible_in_every_invoice(layout):
    failures = []
    for key, invoice in CATALOGUE.items():
        text = visible_text(render_html(invoice, layout))
        failures += [f"{key}: {miss}" for miss in invisible_fields(invoice, text)]
    assert not failures, (
        f"{layout.name}: ground truth claims values this layout never prints:\n  "
        + "\n  ".join(failures)
    )


def test_every_implicit_field_is_a_real_business_term():
    assert set(IMPLICIT) <= set(ALL_FIELDS)


def test_the_audit_catches_a_price_printed_at_the_wrong_precision():
    """The exact defect the first run found, reintroduced by hand."""
    invoice = CATALOGUE["rounding-boundaries"]
    text = visible_text(render_html(invoice, "minimal")).replace("16,665", "16,66")
    assert any("BT-146" in miss for miss in invisible_fields(invoice, text))


def test_the_audit_catches_a_unit_that_is_never_printed():
    invoice = CATALOGUE["fractional-quantities"]
    text = visible_text(render_html(invoice, "modern")).replace("37,5 h", "37,5")
    assert any("BT-130" in miss for miss in invisible_fields(invoice, text))


# --- the same check on real PDFs, for the invoices most likely to overflow -----


@pytest.mark.pdf
@pytest.mark.parametrize("key", ["rounding-boundaries", "fractional-quantities",
                                 "mixed-units-of-measure", "large-amounts"])
def test_prices_and_units_survive_into_the_pdf(key: str, tmp_path: Path):
    """Four-decimal prices and unit words make columns wider; the HTML check alone
    cannot see a value clipped off the page, which is how the ledger layout once
    lost three totals."""
    playwright = pytest.importorskip("playwright.sync_api")
    from pypdf import PdfReader

    invoice = CATALOGUE[key]
    failures = []
    try:
        with PdfRenderer() as renderer:
            for layout in LAYOUTS:
                path = renderer.render(invoice, tmp_path / f"{layout.name}.pdf", layout)
                text = " ".join(
                    " ".join(page.extract_text().split())
                    for page in PdfReader(str(path)).pages
                ).replace("−", "-")
                failures += [f"{layout.name}: {m}" for m in invisible_fields(invoice, text)
                             if "BT-146" in m or "BT-130" in m or "BT-131" in m]
    except playwright.Error as exc:  # pragma: no cover - depends on local install
        pytest.skip(f"Chromium not installed: {exc}")
    assert not failures, "\n".join(failures)
