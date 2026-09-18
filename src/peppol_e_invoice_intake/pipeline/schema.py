"""What the model is asked to return, and nothing more.

Two decisions are baked into this schema, both of which shape the whole pipeline:

**Every number is a string.** JSON has one numeric type and it is a float.
`1832.98` round-trips through a float as `1832.9799999999998`, and EN 16931's
total rules are exact-equality checks. Strings are parsed into `Decimal` at the
boundary, where a malformed value becomes a reported problem instead of a silent
rounding error.

**The model is asked for the totals it can *see*, and never asked to compute
any.** The pipeline recomputes totals from the line items using the same
arithmetic the corpus was built with. The stated totals exist purely to be
disagreed with: if what the document says and what the lines add up to differ,
the extraction is wrong somewhere, and that is worth surfacing rather than
papering over. See docs/decisions/0002-extract-then-compute.md.

**Absent fields are empty strings on the wire, not nulls.** Structured outputs
compile the schema into a grammar for constrained decoding, and every nullable
field becomes a `string | null` union in it. With 38 of them the API rejected the
request outright - "the compiled grammar is too large" - on the first real call.
So the model sees plain required strings, and `_Extracted` turns an empty one
back into None as soon as the JSON is parsed.
"""

from __future__ import annotations

from types import NoneType
from typing import get_args

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class _Extracted(BaseModel):
    """Base for everything the model returns.

    On the wire every field is a plain required string and an empty string means
    "not printed on the document". Here, an empty string in an optional field
    becomes None, so everything downstream keeps working with ordinary Optional
    fields. Required fields keep whatever they were given.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _blank_means_absent(cls, value, info: ValidationInfo):
        if isinstance(value, str) and not value.strip():
            annotation = cls.model_fields[info.field_name].annotation
            if NoneType in get_args(annotation):
                return None
        return value


class ExtractedParty(_Extracted):
    """A seller or buyer as printed on the document."""

    name: str | None = Field(description="Registered or trading name, as printed.")
    street: str | None = Field(description="Street and number, one line.")
    city: str | None = Field(description="City or town.")
    postal_zone: str | None = Field(description="Post code, as printed.")
    country_code: str | None = Field(
        description="ISO 3166-1 alpha-2, uppercase. Infer from the address if unlabelled."
    )
    vat_id: str | None = Field(
        description="VAT identifier including the country prefix, e.g. BE0403040542."
    )
    legal_id: str | None = Field(
        description=(
            "Company or enterprise registration number without a country prefix. "
            "For Belgium this is the 10-digit ondernemingsnummer / numero d'entreprise."
        )
    )
    contact_email: str | None = Field(description="Contact email address, if printed.")


class ExtractedLine(_Extracted):
    """One invoice line, exactly as printed."""

    line_id: str | None = Field(description="Line number as printed, e.g. '1'.")
    name: str | None = Field(description="Short item or service name.")
    description: str | None = Field(
        description="Longer description printed under the name, if any. Otherwise empty."
    )
    quantity: str | None = Field(
        description="Quantity as a plain decimal with a dot, e.g. '37.5'. Not '37,5'."
    )
    unit_code: str | None = Field(
        description=(
            "UN/ECE Recommendation 20 code for the unit PRINTED with the quantity, "
            "in a unit column or right beside the number. Units print as words or "
            "abbreviations in Dutch, French or English: uur / h / hrs is HUR, kg is "
            "KGM, m is MTR, l / L is LTR, dagen / jours / days is DAY, maand / mois / "
            "months is MON, and pieces are C62. Leave empty when no unit is printed "
            "- do not infer one from the item name, even one like 'per month'."
        )
    )
    unit_price: str | None = Field(
        description="Price per unit before VAT, plain decimal with a dot."
    )
    discount: str | None = Field(
        description=(
            "Line-level discount amount as a positive number, if one is printed on "
            "this line. Empty when there is none."
        )
    )
    vat_rate: str | None = Field(
        description="VAT percentage as a number without a percent sign, e.g. '21' or '6'."
    )
    vat_category: str | None = Field(
        description=(
            "UNCL5305 code. S = standard rated. Z = zero rated. E = exempt. "
            "AE = reverse charge (btw verlegd / autoliquidation / medecontractant). "
            "K = intra-community supply (intracommunautaire levering / livraison "
            "intracommunautaire). G = export outside the EU. "
            "Read the wording near the VAT summary, not just the rate: a 0% line is "
            "not automatically Z."
        )
    )


class StatedTotals(_Extracted):
    """The totals printed on the document.

    These are never used as the emitted values. They are the cross-check that
    catches a misread quantity or price, which would otherwise produce a
    perfectly valid invoice carrying the wrong amount.
    """

    line_extension_amount: str | None = Field(
        description="Sum of line amounts before VAT, as printed (total excl. VAT)."
    )
    tax_amount: str | None = Field(description="Total VAT amount, as printed.")
    tax_inclusive_amount: str | None = Field(
        description="Total including VAT, as printed."
    )
    prepaid_amount: str | None = Field(
        description="Amount already paid or prepaid, as printed. Empty if none."
    )
    payable_amount: str | None = Field(
        description="Final amount due for payment, as printed."
    )


class VatBreakdownRow(_Extracted):
    """One row of the printed VAT summary, used as a second cross-check."""

    vat_rate: str | None = Field(description="Rate as a number, e.g. '21'.")
    taxable_amount: str | None = Field(description="Base amount for this rate.")
    tax_amount: str | None = Field(description="VAT amount for this rate.")


class Uncertainty(_Extracted):
    """Something the model could not resolve confidently.

    The brief asks that anything the pipeline cannot map confidently be surfaced
    for a human rather than guessed, so this is a first-class part of the output
    rather than free text appended to a log.
    """

    field: str = Field(
        description="Which field this concerns, e.g. 'BT-48' or 'line 3 unit_price'."
    )
    problem: str = Field(description="What was unclear, in one sentence.")
    printed_text: str | None = Field(
        description="The text on the document that caused the doubt, if any."
    )


class ExtractedInvoice(_Extracted):
    """Everything read off one invoice PDF."""

    invoice_number: str | None = Field(description="The invoice number, as printed.")
    issue_date: str | None = Field(
        description=(
            "Invoice date in ISO format, YYYY-MM-DD. Belgian invoices print "
            "DD/MM/YYYY, so 02/03/2026 is 2026-03-02, not 2026-02-03."
        )
    )
    due_date: str | None = Field(description="Payment due date in ISO format, or empty.")
    delivery_date: str | None = Field(
        description="Actual delivery or supply date in ISO format, or empty."
    )
    currency: str | None = Field(description="ISO 4217 code, e.g. EUR.")
    buyer_reference: str | None = Field(
        description=(
            "The buyer's own reference for this invoice: 'uw referentie', "
            "'votre reference', 'your reference', 'customer reference'."
        )
    )
    order_reference: str | None = Field(
        description="Purchase order or order form number: 'bestelbon', 'bon de commande'."
    )
    note: str | None = Field(description="Free-text note on the invoice, if any.")

    supplier: ExtractedParty = Field(description="The seller issuing the invoice.")
    customer: ExtractedParty = Field(description="The buyer being invoiced.")

    lines: list[ExtractedLine] = Field(
        description="Every invoice line, in the order printed. Do not merge or omit lines."
    )

    stated_totals: StatedTotals = Field(description="Totals as printed on the document.")
    vat_breakdown: list[VatBreakdownRow] = Field(
        description="Rows of the printed VAT summary table, if there is one."
    )

    iban: str | None = Field(description="Payment account IBAN, if printed.")
    payment_reference: str | None = Field(
        description="Structured communication or payment reference, if printed."
    )
    payment_terms: str | None = Field(
        description="Payment terms text, e.g. 'binnen 30 dagen'."
    )
    exemption_reason: str | None = Field(
        description=(
            "The stated legal reason for a VAT exemption or reverse charge, verbatim. "
            "Empty when every line is standard rated."
        )
    )

    uncertainties: list[Uncertainty] = Field(
        description=(
            "Anything you could not read confidently. Prefer recording an "
            "uncertainty over guessing a value."
        )
    )
