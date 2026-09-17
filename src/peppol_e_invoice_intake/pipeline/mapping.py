"""Turn an `ExtractedInvoice` into the `Invoice` model, deterministically.

The model reads; this module computes. Totals, VAT breakdown rows and rounding
all come from the same arithmetic the corpus was built with, so the emitted
document satisfies BR-CO-10 through BR-CO-17 by construction rather than by the
model getting the sums right.

That trade has a cost, and it is the reason `reconcile` exists: if the pipeline
simply recomputed everything, a misread quantity would produce a perfectly valid
invoice carrying the wrong amount - worse than an invalid one, because nothing
downstream would notice. So the totals printed on the document are extracted too,
and compared against the computed ones. A disagreement is surfaced, never
silently resolved.

See docs/decisions/0002-extract-then-compute.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from ..corpus.identifiers import is_valid_enterprise_number
from ..corpus.model import Invoice, InvoiceLine, Language, Party, VatCategory
from .schema import ExtractedInvoice, ExtractedLine, ExtractedParty


class ProblemKind(StrEnum):
    MISSING = "missing"
    UNPARSABLE = "unparsable"
    DERIVED = "derived"
    RECONCILIATION = "reconciliation"
    UNCERTAINTY = "uncertainty"


@dataclass(frozen=True)
class Problem:
    """Something a person should look at.

    `needs_human` separates "this document cannot be trusted as emitted" from
    "this was filled in by a rule you should know about".
    """

    kind: ProblemKind
    field: str
    detail: str
    printed: str | None = None
    needs_human: bool = True

    def __str__(self) -> str:
        where = f" (document says {self.printed!r})" if self.printed else ""
        return f"[{self.kind.value}] {self.field}: {self.detail}{where}"


@dataclass
class MappingResult:
    invoice: Invoice | None
    problems: list[Problem] = field(default_factory=list)

    @property
    def mapped(self) -> bool:
        return self.invoice is not None

    @property
    def blocking(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.needs_human]


@dataclass
class _Collector:
    problems: list[Problem] = field(default_factory=list)

    def add(self, kind: ProblemKind, field_name: str, detail: str, **kwargs) -> None:
        self.problems.append(Problem(kind, field_name, detail, **kwargs))

    def decimal(self, value: str | None, field_name: str) -> Decimal | None:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError):
            self.add(
                ProblemKind.UNPARSABLE,
                field_name,
                "not a decimal number in canonical form",
                printed=text,
            )
            return None

    def date(self, value: str | None, field_name: str) -> date | None:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            self.add(
                ProblemKind.UNPARSABLE, field_name, "not an ISO date", printed=text
            )
            return None


def _endpoint_for(party: ExtractedParty, role: str, collector: _Collector) -> tuple[str, str]:
    """Derive a Peppol electronic address, which invoices do not print.

    BT-34 and BT-49 identify where a document is routed on the network. No paper
    invoice carries them, so they have to come from an identifier that is printed.
    For Belgium the enterprise number under scheme 0208 is the normal choice, and
    the VAT identifier under 9925 is the fallback. This is a derivation, recorded
    as such, not an extraction.
    """
    country = (party.country_code or "").upper()
    vat_id = (party.vat_id or "").replace(" ", "")
    legal_id = (party.legal_id or "").replace(".", "").replace(" ", "")

    if country == "BE":
        if legal_id and is_valid_enterprise_number(legal_id):
            collector.add(
                ProblemKind.DERIVED,
                f"{role} BT-34/BT-49",
                "electronic address derived from the enterprise number (scheme 0208)",
                needs_human=False,
            )
            return legal_id, "0208"
        if vat_id.startswith("BE") and is_valid_enterprise_number(vat_id[2:]):
            collector.add(
                ProblemKind.DERIVED,
                f"{role} BT-34/BT-49",
                "electronic address derived from the VAT identifier (scheme 0208)",
                needs_human=False,
            )
            return vat_id[2:], "0208"

    if vat_id:
        scheme = {"NL": "9944", "DE": "9930", "FR": "9957", "IT": "9906"}.get(country, "9925")
        collector.add(
            ProblemKind.DERIVED,
            f"{role} BT-34/BT-49",
            f"electronic address derived from the VAT identifier (scheme {scheme})",
            needs_human=False,
        )
        return vat_id, scheme

    collector.add(
        ProblemKind.MISSING,
        f"{role} BT-34/BT-49",
        "no VAT or registration number printed, so no electronic address can be "
        "derived; a person must supply the Peppol participant identifier",
    )
    return "", "0208"


def _party(party: ExtractedParty, role: str, collector: _Collector) -> Party:
    if not party.name:
        collector.add(ProblemKind.MISSING, f"{role} name", "no name found on the document")

    country = (party.country_code or "").upper()
    if not country:
        collector.add(
            ProblemKind.MISSING, f"{role} country", "no country could be read or inferred"
        )

    legal_id = (party.legal_id or "").replace(".", "").replace(" ", "") or None
    legal_scheme = None
    if legal_id and country == "BE":
        if is_valid_enterprise_number(legal_id):
            legal_scheme = "0208"
        else:
            # PEPPOL-COMMON-R043 will reject this, and it is better to say why here
            # than to let the rule id be the only explanation.
            collector.add(
                ProblemKind.UNPARSABLE,
                f"{role} BT-30/BT-47",
                "Belgian enterprise number fails the mod-97 check, so it was probably "
                "misread",
                printed=legal_id,
            )
            legal_scheme = "0208"

    endpoint_id, endpoint_scheme = _endpoint_for(party, role, collector)

    return Party(
        name=party.name or "",
        street=party.street or "",
        city=party.city or "",
        postal_zone=party.postal_zone or "",
        country=country or "BE",
        vat_id=(party.vat_id or "").replace(" ", "") or None,
        legal_id=legal_id,
        legal_scheme=legal_scheme,
        endpoint_id=endpoint_id,
        endpoint_scheme=endpoint_scheme,
        contact_email=party.contact_email,
    )


def _line(extracted: ExtractedLine, index: int, collector: _Collector) -> InvoiceLine | None:
    label = f"line {extracted.line_id or index}"

    quantity = collector.decimal(extracted.quantity, f"{label} BT-129")
    unit_price = collector.decimal(extracted.unit_price, f"{label} BT-146")
    if quantity is None or unit_price is None:
        collector.add(
            ProblemKind.MISSING,
            label,
            "line dropped: quantity or unit price could not be read",
        )
        return None

    rate = collector.decimal(extracted.vat_rate, f"{label} BT-152")
    if rate is None:
        collector.add(
            ProblemKind.MISSING,
            f"{label} BT-152",
            "no VAT rate read; defaulted to 21%, the Belgian standard rate",
        )
        rate = Decimal("21")

    raw_category = (extracted.vat_category or "").strip().upper()
    try:
        category = VatCategory(raw_category)
    except ValueError:
        # A zero rate with no category is much more likely to be an exemption the
        # model failed to name than a genuine standard-rated zero, so say so
        # rather than picking one quietly.
        category = VatCategory.STANDARD if rate > 0 else VatCategory.ZERO_RATED
        collector.add(
            ProblemKind.MISSING if not raw_category else ProblemKind.UNPARSABLE,
            f"{label} BT-151",
            f"VAT category not read; assumed {category.value} from the {rate}% rate",
            printed=raw_category or None,
            needs_human=rate == 0,
        )

    return InvoiceLine(
        line_id=str(extracted.line_id or index),
        name=extracted.name or "",
        description=extracted.description,
        quantity=quantity,
        unit_code=(extracted.unit_code or "C62").strip().upper(),
        unit_price=unit_price,
        vat_rate=rate,
        vat_category=category,
        discount=collector.decimal(extracted.discount, f"{label} BT-136"),
    )


#: Which computed total is checked against which extracted one.
RECONCILED_TOTALS = (
    ("BT-106", "sum of line net amounts", "line_extension_amount", "line_extension_amount"),
    ("BT-110", "total VAT", "tax_amount", "tax_amount"),
    ("BT-112", "total including VAT", "tax_inclusive_amount", "tax_inclusive_amount"),
    ("BT-115", "amount due for payment", "payable_amount", "payable_amount"),
)


def reconcile(invoice: Invoice, extracted: ExtractedInvoice) -> list[Problem]:
    """Compare the computed totals against the ones printed on the document.

    This is the check that makes deterministic mapping safe. Without it a misread
    quantity yields a valid invoice for the wrong amount, and validation - which
    only ever sees the computed, self-consistent numbers - would pass it.
    """
    problems: list[Problem] = []
    collector = _Collector()

    for term, label, computed_attr, stated_attr in RECONCILED_TOTALS:
        stated = collector.decimal(getattr(extracted.stated_totals, stated_attr), term)
        if stated is None:
            continue
        computed = getattr(invoice, computed_attr)
        if computed != stated:
            problems.append(
                Problem(
                    ProblemKind.RECONCILIATION,
                    term,
                    f"{label} computed from the lines is {computed}, but the document "
                    f"states {stated} (difference {computed - stated})",
                    printed=str(stated),
                )
            )

    # The printed VAT summary is an independent second opinion on the rates and
    # bases, so a disagreement here usually localises which line was misread.
    stated_rows = {
        rate: (base, tax)
        for row in extracted.vat_breakdown
        if (rate := collector.decimal(row.vat_rate, "BT-119")) is not None
        and (base := collector.decimal(row.taxable_amount, "BT-116")) is not None
        and (tax := collector.decimal(row.tax_amount, "BT-117")) is not None
    }
    for subtotal in invoice.tax_subtotals:
        if (stated := stated_rows.get(subtotal.rate)) is None:
            continue
        stated_base, stated_tax = stated
        if subtotal.taxable_amount != stated_base or subtotal.tax_amount != stated_tax:
            problems.append(
                Problem(
                    ProblemKind.RECONCILIATION,
                    f"BG-23 at {subtotal.rate}%",
                    f"VAT breakdown computed as base {subtotal.taxable_amount} / VAT "
                    f"{subtotal.tax_amount}, but the document states base {stated_base} "
                    f"/ VAT {stated_tax}",
                )
            )

    return problems + collector.problems


def map_to_invoice(
    extracted: ExtractedInvoice, *, language: Language = Language.NL
) -> MappingResult:
    """Build an `Invoice` from an extraction, collecting everything questionable."""
    collector = _Collector()

    for uncertainty in extracted.uncertainties:
        collector.add(
            ProblemKind.UNCERTAINTY,
            uncertainty.field,
            uncertainty.problem,
            printed=uncertainty.printed_text,
        )

    lines = [
        mapped
        for index, extracted_line in enumerate(extracted.lines, start=1)
        if (mapped := _line(extracted_line, index, collector)) is not None
    ]
    if not lines:
        collector.add(
            ProblemKind.MISSING, "BG-25", "no invoice line could be read from the document"
        )
        return MappingResult(None, collector.problems)

    issue_date = collector.date(extracted.issue_date, "BT-2")
    if issue_date is None:
        collector.add(
            ProblemKind.MISSING, "BT-2", "no issue date could be read; the document cannot "
            "be mapped without one"
        )
        return MappingResult(None, collector.problems)

    if not extracted.invoice_number:
        collector.add(ProblemKind.MISSING, "BT-1", "no invoice number could be read")
    if not (extracted.buyer_reference or extracted.order_reference):
        # PEPPOL-EN16931-R003 requires one of the two, and neither can be invented.
        collector.add(
            ProblemKind.MISSING,
            "BT-10/BT-13",
            "neither a buyer reference nor an order reference is printed; Peppol "
            "requires one of them and it must come from a person",
        )

    invoice = Invoice(
        number=extracted.invoice_number or "",
        issue_date=issue_date,
        due_date=collector.date(extracted.due_date, "BT-9"),
        currency=(extracted.currency or "EUR").strip().upper(),
        buyer_reference=extracted.buyer_reference,
        order_reference=extracted.order_reference,
        supplier=_party(extracted.supplier, "seller", collector),
        customer=_party(extracted.customer, "buyer", collector),
        lines=lines,
        note=extracted.note,
        delivery_date=collector.date(extracted.delivery_date, "BT-72"),
        delivery_country=(extracted.customer.country_code or "").upper() or None,
        iban=extracted.iban,
        payment_reference=extracted.payment_reference,
        payment_terms=extracted.payment_terms,
        prepaid_amount=collector.decimal(extracted.stated_totals.prepaid_amount, "BT-113")
        or Decimal("0.00"),
        exemption_reason=extracted.exemption_reason,
        language=language,
    )

    problems = collector.problems + reconcile(invoice, extracted)
    return MappingResult(invoice, problems)
