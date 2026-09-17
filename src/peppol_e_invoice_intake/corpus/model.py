"""The invoice model the corpus is generated from.

Phase 2 inverts the usual problem: instead of finding PDFs and labelling them by
hand, we start from a model, emit both a UBL document and a PDF from it, and get
perfect field-level ground truth for free.

That only works if the model is the single source of truth for *amounts* as well
as text. Tax arithmetic is the hardest part of EN 16931 compliance, so it lives
here, in one place, computed once - not duplicated between the XML emitter and the
PDF template where the two could silently disagree.

All money is `Decimal`, quantised to two places with ROUND_HALF_UP at every step
that EN 16931 defines as a rounding point. Floats are never used: BR-CO-14 through
BR-CO-17 are exact-equality rules, and binary floating point cannot satisfy them
reliably.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

TWO_PLACES = Decimal("0.01")


def money(value: Decimal | int | str) -> Decimal:
    """Round to two decimals the way EN 16931 expects."""
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class VatCategory(StrEnum):
    """UNCL5305 codes, restricted to what Belgian B2B invoices actually use."""

    STANDARD = "S"
    ZERO_RATED = "Z"
    EXEMPT = "E"
    REVERSE_CHARGE = "AE"
    INTRA_COMMUNITY = "K"
    EXPORT = "G"

    @property
    def needs_exemption_reason(self) -> bool:
        """Categories where EN 16931 requires a stated reason (BR-AE-10, BR-IC-10, ...)."""
        return self in {
            VatCategory.ZERO_RATED,
            VatCategory.EXEMPT,
            VatCategory.REVERSE_CHARGE,
            VatCategory.INTRA_COMMUNITY,
            VatCategory.EXPORT,
        }


class Language(StrEnum):
    NL = "nl"
    FR = "fr"
    EN = "en"


class Party(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    street: str
    city: str
    postal_zone: str
    country: str = "BE"

    #: BT-31 / BT-48, e.g. "BE0403040542"
    vat_id: str | None = None
    #: BT-30 / BT-47 legal registration identifier, e.g. a 0208 enterprise number
    legal_id: str | None = None
    legal_scheme: str | None = "0208"
    #: BT-34 / BT-49, the Peppol routing address
    endpoint_id: str
    endpoint_scheme: str = "0208"

    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None

    @property
    def registration_name(self) -> str:
        return self.name


class InvoiceLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_id: str
    name: str
    description: str | None = None
    quantity: Decimal
    unit_code: str = "C62"
    unit_price: Decimal
    vat_category: VatCategory = VatCategory.STANDARD
    vat_rate: Decimal = Decimal("21")
    #: BT-136, a line-level discount applied before VAT
    discount: Decimal | None = None

    @property
    def gross_amount(self) -> Decimal:
        return money(self.quantity * self.unit_price)

    @property
    def net_amount(self) -> Decimal:
        """BT-131. The line total that must sum to BT-106 under BR-CO-10."""
        return money(self.gross_amount - (self.discount or Decimal(0)))


class TaxSubtotal(BaseModel):
    """BG-23, one VAT breakdown row."""

    model_config = ConfigDict(frozen=True)

    category: VatCategory
    rate: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal
    exemption_reason: str | None = None


class Invoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: str
    issue_date: date
    due_date: date | None = None
    currency: str = "EUR"
    buyer_reference: str | None = None
    order_reference: str | None = None

    supplier: Party
    customer: Party
    lines: list[InvoiceLine] = Field(min_length=1)

    note: str | None = None
    delivery_date: date | None = None
    payment_means_code: str = "30"
    iban: str | None = None
    payment_reference: str | None = None
    payment_terms: str | None = None

    #: BT-113, already paid. Subtracted from the payable amount under BR-CO-16.
    prepaid_amount: Decimal = Decimal("0.00")
    #: Reason text for every non-standard VAT category on this invoice.
    exemption_reason: str | None = None

    #: Presentation only - never affects the UBL, only which template strings are used.
    language: Language = Language.NL

    # --- computed totals ------------------------------------------------------
    # Ordered so each one builds on the last, mirroring BR-CO-10 through BR-CO-16.

    @property
    def line_extension_amount(self) -> Decimal:
        """BT-106. BR-CO-10: must equal the sum of line net amounts."""
        return money(sum((line.net_amount for line in self.lines), Decimal(0)))

    @property
    def tax_subtotals(self) -> list[TaxSubtotal]:
        """BG-23 rows, one per distinct (category, rate) pair.

        BR-CO-17 defines each row's tax amount as taxable x rate / 100 rounded to
        two decimals - computed from the *group* total, not by summing per-line
        tax, which would round differently and fail the rule.
        """
        groups: dict[tuple[VatCategory, Decimal], Decimal] = {}
        for line in self.lines:
            key = (line.vat_category, line.vat_rate)
            groups[key] = groups.get(key, Decimal(0)) + line.net_amount

        subtotals = []
        for (category, rate), taxable in groups.items():
            taxable = money(taxable)
            subtotals.append(
                TaxSubtotal(
                    category=category,
                    rate=rate,
                    taxable_amount=taxable,
                    tax_amount=money(taxable * rate / Decimal(100)),
                    exemption_reason=(
                        self.exemption_reason if category.needs_exemption_reason else None
                    ),
                )
            )
        return sorted(subtotals, key=lambda s: (s.category.value, s.rate))

    @property
    def tax_amount(self) -> Decimal:
        """BT-110. BR-CO-14: the sum of the breakdown rows."""
        return money(sum((s.tax_amount for s in self.tax_subtotals), Decimal(0)))

    @property
    def tax_exclusive_amount(self) -> Decimal:
        """BT-109. BR-CO-13, with no document-level allowances or charges in v1."""
        return self.line_extension_amount

    @property
    def tax_inclusive_amount(self) -> Decimal:
        """BT-112. BR-CO-15."""
        return money(self.tax_exclusive_amount + self.tax_amount)

    @property
    def payable_amount(self) -> Decimal:
        """BT-115. BR-CO-16."""
        return money(self.tax_inclusive_amount - money(self.prepaid_amount))

    @property
    def has_multiple_vat_rates(self) -> bool:
        return len(self.tax_subtotals) > 1

    def line(self, line_id: str) -> InvoiceLine:
        return next(line for line in self.lines if line.line_id == line_id)
