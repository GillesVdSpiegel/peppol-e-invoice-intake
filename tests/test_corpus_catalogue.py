"""The catalogue itself: coverage, identifier validity, and the validation gate."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from conftest import requires_artefacts
from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.corpus.identifiers import (
    is_valid_enterprise_number,
    is_valid_gln,
)
from peppol_e_invoice_intake.corpus.model import Language, VatCategory
from peppol_e_invoice_intake.corpus.ubl import to_xml
from peppol_e_invoice_intake.validation import validate


@pytest.mark.artefacts
@requires_artefacts
@pytest.mark.parametrize("key", list(CATALOGUE), ids=list(CATALOGUE))
def test_every_catalogue_invoice_produces_valid_ubl(key: str, tmp_path: Path):
    """The gate that makes the corpus worth anything.

    "Start from known-good UBL" only means something if every document is checked
    against the official rule sets. Six of these failed the first time they were
    run - a zero-rated invoice carrying an exemption reason BR-Z-10 forbids, an
    intra-community supply missing the deliver-to country, VAT numbers labelled
    as GLNs, and prices rounded to two decimals so the line total no longer
    matched quantity times price.
    """
    document = tmp_path / f"{key}.xml"
    document.write_bytes(to_xml(CATALOGUE[key]))
    result = validate(document)
    assert result.is_valid, f"{key}:\n" + "\n".join(str(f) for f in result.blocking)


def test_catalogue_size_is_what_the_brief_asked_for():
    assert len(CATALOGUE) == 20


def test_every_language_is_represented():
    counts = Counter(invoice.language for invoice in CATALOGUE.values())
    assert set(counts) == set(Language)
    assert min(counts.values()) >= 3, f"a language is barely present: {counts}"


def test_the_hard_vat_categories_are_covered():
    """The brief calls out reverse charge and intra-community supply specifically;
    both have extra EN 16931 rules that ordinary standard-rated invoices never hit."""
    categories = {
        subtotal.category
        for invoice in CATALOGUE.values()
        for subtotal in invoice.tax_subtotals
    }
    for required in (
        VatCategory.STANDARD,
        VatCategory.REVERSE_CHARGE,
        VatCategory.INTRA_COMMUNITY,
        VatCategory.EXEMPT,
        VatCategory.ZERO_RATED,
        VatCategory.EXPORT,
    ):
        assert required in categories, f"no invoice exercises VAT category {required.value}"


def test_multiple_vat_rates_on_one_document_are_covered():
    assert any(invoice.has_multiple_vat_rates for invoice in CATALOGUE.values())


def test_line_discounts_are_covered():
    assert any(
        invoice_line.discount is not None
        for invoice in CATALOGUE.values()
        for invoice_line in invoice.lines
    )


def test_a_prepaid_invoice_is_covered():
    assert any(invoice.prepaid_amount for invoice in CATALOGUE.values())


def test_an_invoice_runs_past_a_single_page():
    assert max(len(invoice.lines) for invoice in CATALOGUE.values()) >= 40


def test_fractional_quantities_and_non_default_units_are_covered():
    units = {
        invoice_line.unit_code
        for invoice in CATALOGUE.values()
        for invoice_line in invoice.lines
    }
    assert {"HUR", "KGM", "MTR", "LTR"} <= units

    assert any(
        invoice_line.quantity % 1
        for invoice in CATALOGUE.values()
        for invoice_line in invoice.lines
    ), "no invoice uses a fractional quantity"


def test_prices_with_more_than_two_decimals_are_covered():
    """PEPPOL-EN16931-R120 only bites when a price carries a third decimal."""
    assert any(
        -invoice_line.unit_price.as_tuple().exponent > 2
        for invoice in CATALOGUE.values()
        for invoice_line in invoice.lines
    )


def test_every_belgian_enterprise_number_passes_mod97():
    for key, invoice in CATALOGUE.items():
        for role, party in (("supplier", invoice.supplier), ("customer", invoice.customer)):
            if party.legal_scheme == "0208" and party.legal_id:
                assert is_valid_enterprise_number(party.legal_id), f"{key} {role}"


def test_every_gln_endpoint_passes_the_gs1_check():
    for key, invoice in CATALOGUE.items():
        for party in (invoice.supplier, invoice.customer):
            if party.endpoint_scheme == "0088":
                assert is_valid_gln(party.endpoint_id), f"{key}: {party.endpoint_id}"


def test_buyer_references_are_distinct():
    """One shared reference would let extraction score on BT-10 by memorisation."""
    references = [
        invoice.buyer_reference for invoice in CATALOGUE.values() if invoice.buyer_reference
    ]
    assert len(set(references)) == len(references)


def test_invoice_numbers_are_distinct():
    numbers = [invoice.number for invoice in CATALOGUE.values()]
    assert len(set(numbers)) == len(numbers)


def test_issue_dates_are_spread_out():
    dates = {invoice.issue_date for invoice in CATALOGUE.values()}
    assert len(dates) >= 15, "a catalogue dated on one day makes BT-2 trivial"


def test_exemption_reasons_are_written_in_the_invoice_language():
    """A French invoice carrying a Dutch exemption reason is not a document any
    supplier would send, and the corpus should not contain one."""
    markers = {
        Language.NL: ("btw", "vrijgesteld", "artikel"),
        Language.FR: ("tva", "exon", "article", "autoliquidation"),
        Language.EN: ("vat", "exempt", "article", "supply"),
    }
    for key, invoice in CATALOGUE.items():
        if not invoice.exemption_reason:
            continue
        text = invoice.exemption_reason.lower()
        assert any(marker in text for marker in markers[invoice.language]), (
            f"{key}: exemption reason does not look like {invoice.language.value}"
        )


def test_payment_terms_are_written_in_the_invoice_language():
    expected = {
        Language.NL: ("dagen", "contant"),
        Language.FR: ("jours", "comptant"),
        Language.EN: ("days", "receipt"),
    }
    for key, invoice in CATALOGUE.items():
        if not invoice.payment_terms:
            continue
        text = invoice.payment_terms.lower()
        assert any(marker in text for marker in expected[invoice.language]), (
            f"{key}: payment terms do not look like {invoice.language.value}"
        )
