"""Turn a corpus `Invoice` into the extraction a flawless reader would return.

This is the pipeline's upper bound. Feeding it through mapping isolates the
deterministic half: anything that fails here is a mapping bug, because the
reading was perfect by construction. It also lets the whole mapping and repair
layer be tested without a network call or a cent of spend.
"""

from __future__ import annotations

from peppol_e_invoice_intake.corpus.model import Invoice, InvoiceLine, Party
from peppol_e_invoice_intake.pipeline.schema import (
    ExtractedInvoice,
    ExtractedLine,
    ExtractedParty,
    StatedTotals,
    VatBreakdownRow,
)


def _party(party: Party) -> ExtractedParty:
    return ExtractedParty(
        name=party.name,
        street=party.street,
        city=party.city,
        postal_zone=party.postal_zone,
        country_code=party.country,
        vat_id=party.vat_id,
        legal_id=party.legal_id,
        contact_email=party.contact_email,
    )


def _line(line: InvoiceLine) -> ExtractedLine:
    return ExtractedLine(
        line_id=line.line_id,
        name=line.name,
        description=line.description,
        quantity=f"{line.quantity:f}",
        unit_code=line.unit_code,
        unit_price=f"{line.unit_price:f}",
        discount=f"{line.discount:f}" if line.discount is not None else None,
        vat_rate=f"{line.vat_rate:f}",
        vat_category=line.vat_category.value,
    )


def perfect_extraction(invoice: Invoice) -> ExtractedInvoice:
    """What the model would return if it read every field correctly."""
    return ExtractedInvoice(
        invoice_number=invoice.number,
        issue_date=invoice.issue_date.isoformat(),
        due_date=invoice.due_date.isoformat() if invoice.due_date else None,
        delivery_date=invoice.delivery_date.isoformat() if invoice.delivery_date else None,
        currency=invoice.currency,
        buyer_reference=invoice.buyer_reference,
        order_reference=invoice.order_reference,
        note=invoice.note,
        supplier=_party(invoice.supplier),
        customer=_party(invoice.customer),
        lines=[_line(line) for line in invoice.lines],
        stated_totals=StatedTotals(
            line_extension_amount=f"{invoice.line_extension_amount:f}",
            tax_amount=f"{invoice.tax_amount:f}",
            tax_inclusive_amount=f"{invoice.tax_inclusive_amount:f}",
            prepaid_amount=(
                f"{invoice.prepaid_amount:f}" if invoice.prepaid_amount else None
            ),
            payable_amount=f"{invoice.payable_amount:f}",
        ),
        vat_breakdown=[
            VatBreakdownRow(
                vat_rate=f"{subtotal.rate:f}",
                taxable_amount=f"{subtotal.taxable_amount:f}",
                tax_amount=f"{subtotal.tax_amount:f}",
            )
            for subtotal in invoice.tax_subtotals
        ],
        iban=invoice.iban,
        payment_reference=invoice.payment_reference,
        payment_terms=invoice.payment_terms,
        exemption_reason=invoice.exemption_reason,
        uncertainties=[],
    )
