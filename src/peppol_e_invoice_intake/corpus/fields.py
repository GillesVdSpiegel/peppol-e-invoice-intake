"""EN 16931 business terms mapped to UBL locations.

This table has two jobs, and they have to be the same table or the numbers lie:

1. Phase 2 reads ground truth out of a generated invoice with it.
2. Phase 4 scores extracted output against that ground truth with it.

Ground truth is read back out of the emitted UBL rather than taken from the model
the UBL was built from. It costs one extra parse and buys something worth having:
the ground truth describes what the *document* actually says, so a bug in the
emitter shows up as a corpus that fails validation or mismatches, instead of
being silently baked into the labels as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from lxml import etree

CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS = {"inv": INV, "cac": CAC, "cbc": CBC}


class Kind(StrEnum):
    """How a value is compared when scoring. Amounts must not be string-compared:
    '1500.0' and '1500.00' are the same number and a differently-formatted
    extraction is not an extraction error."""

    TEXT = "text"
    CODE = "code"
    DATE = "date"
    AMOUNT = "amount"
    QUANTITY = "quantity"


@dataclass(frozen=True)
class Field:
    bt: str
    name: str
    xpath: str
    kind: Kind = Kind.TEXT

    def read(self, node: etree._Element) -> str | None:
        matches = node.xpath(self.xpath, namespaces=NS)
        if not matches:
            return None
        value = matches[0]
        text = value if isinstance(value, str) else (value.text or "")
        text = text.strip()
        return text or None


DOCUMENT_FIELDS: tuple[Field, ...] = (
    Field("BT-1", "Invoice number", "cbc:ID"),
    Field("BT-2", "Issue date", "cbc:IssueDate", Kind.DATE),
    Field("BT-3", "Invoice type code", "cbc:InvoiceTypeCode", Kind.CODE),
    Field("BT-5", "Currency", "cbc:DocumentCurrencyCode", Kind.CODE),
    Field("BT-9", "Due date", "cbc:DueDate", Kind.DATE),
    Field("BT-10", "Buyer reference", "cbc:BuyerReference"),
    Field("BT-13", "Purchase order reference", "cac:OrderReference/cbc:ID"),
    Field("BT-22", "Invoice note", "cbc:Note"),
    # Seller
    Field("BT-27", "Seller name", "cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name"),
    Field(
        "BT-30",
        "Seller legal registration identifier",
        "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyID",
    ),
    Field(
        "BT-31",
        "Seller VAT identifier",
        "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID",
    ),
    Field(
        "BT-34",
        "Seller electronic address",
        "cac:AccountingSupplierParty/cac:Party/cbc:EndpointID",
    ),
    Field(
        "BT-35",
        "Seller address line 1",
        "cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:StreetName",
    ),
    Field(
        "BT-37",
        "Seller city",
        "cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:CityName",
    ),
    Field(
        "BT-38",
        "Seller post code",
        "cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:PostalZone",
    ),
    Field(
        "BT-40",
        "Seller country code",
        "cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cac:Country"
        "/cbc:IdentificationCode",
        Kind.CODE,
    ),
    # Buyer
    Field("BT-44", "Buyer name", "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name"),
    Field(
        "BT-47",
        "Buyer legal registration identifier",
        "cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyID",
    ),
    Field(
        "BT-48",
        "Buyer VAT identifier",
        "cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID",
    ),
    Field(
        "BT-49",
        "Buyer electronic address",
        "cac:AccountingCustomerParty/cac:Party/cbc:EndpointID",
    ),
    Field(
        "BT-50",
        "Buyer address line 1",
        "cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:StreetName",
    ),
    Field(
        "BT-52",
        "Buyer city",
        "cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:CityName",
    ),
    Field(
        "BT-53",
        "Buyer post code",
        "cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:PostalZone",
    ),
    Field(
        "BT-55",
        "Buyer country code",
        "cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cac:Country"
        "/cbc:IdentificationCode",
        Kind.CODE,
    ),
    # Delivery and payment
    Field("BT-72", "Actual delivery date", "cac:Delivery/cbc:ActualDeliveryDate", Kind.DATE),
    Field("BT-83", "Remittance information", "cac:PaymentMeans/cbc:PaymentID"),
    Field(
        "BT-84",
        "Payment account identifier",
        "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID",
    ),
    # Totals
    Field(
        "BT-106",
        "Sum of line net amounts",
        "cac:LegalMonetaryTotal/cbc:LineExtensionAmount",
        Kind.AMOUNT,
    ),
    Field(
        "BT-109",
        "Total amount without VAT",
        "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount",
        Kind.AMOUNT,
    ),
    Field("BT-110", "Invoice total VAT amount", "cac:TaxTotal/cbc:TaxAmount", Kind.AMOUNT),
    Field(
        "BT-112",
        "Total amount with VAT",
        "cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount",
        Kind.AMOUNT,
    ),
    Field("BT-113", "Paid amount", "cac:LegalMonetaryTotal/cbc:PrepaidAmount", Kind.AMOUNT),
    Field("BT-115", "Amount due for payment", "cac:LegalMonetaryTotal/cbc:PayableAmount", Kind.AMOUNT),
)

LINE_FIELDS: tuple[Field, ...] = (
    Field("BT-126", "Invoice line identifier", "cbc:ID"),
    Field("BT-129", "Invoiced quantity", "cbc:InvoicedQuantity", Kind.QUANTITY),
    Field("BT-130", "Invoiced quantity unit of measure", "cbc:InvoicedQuantity/@unitCode", Kind.CODE),
    Field("BT-131", "Invoice line net amount", "cbc:LineExtensionAmount", Kind.AMOUNT),
    Field("BT-136", "Invoice line allowance amount", "cac:AllowanceCharge/cbc:Amount", Kind.AMOUNT),
    Field("BT-146", "Item net price", "cac:Price/cbc:PriceAmount", Kind.AMOUNT),
    Field("BT-153", "Item name", "cac:Item/cbc:Name"),
    Field("BT-154", "Item description", "cac:Item/cbc:Description"),
    Field("BT-151", "Item VAT category code", "cac:Item/cac:ClassifiedTaxCategory/cbc:ID", Kind.CODE),
    Field(
        "BT-152",
        "Item VAT rate",
        "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent",
        Kind.QUANTITY,
    ),
)

VAT_BREAKDOWN_FIELDS: tuple[Field, ...] = (
    Field("BT-116", "VAT category taxable amount", "cbc:TaxableAmount", Kind.AMOUNT),
    Field("BT-117", "VAT category tax amount", "cbc:TaxAmount", Kind.AMOUNT),
    Field("BT-118", "VAT category code", "cac:TaxCategory/cbc:ID", Kind.CODE),
    Field("BT-119", "VAT category rate", "cac:TaxCategory/cbc:Percent", Kind.QUANTITY),
    Field(
        "BT-120",
        "VAT exemption reason text",
        "cac:TaxCategory/cbc:TaxExemptionReason",
    ),
)

ALL_FIELDS: dict[str, Field] = {
    field.bt: field for field in (*DOCUMENT_FIELDS, *LINE_FIELDS, *VAT_BREAKDOWN_FIELDS)
}


def _read_group(node: etree._Element, fields: tuple[Field, ...]) -> dict[str, str]:
    values = {}
    for field in fields:
        if (value := field.read(node)) is not None:
            values[field.bt] = value
    return values


def extract_ground_truth(xml: bytes | str) -> dict:
    """Read every mapped business term out of a UBL invoice.

    Absent fields are omitted rather than recorded as null, so scoring can tell
    'the document does not contain this' apart from 'the model failed to find it'.
    """
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    root = etree.fromstring(data)

    return {
        "document": _read_group(root, DOCUMENT_FIELDS),
        "lines": [
            _read_group(node, LINE_FIELDS)
            for node in root.xpath("cac:InvoiceLine", namespaces=NS)
        ],
        "vat_breakdown": [
            _read_group(node, VAT_BREAKDOWN_FIELDS)
            for node in root.xpath("cac:TaxTotal/cac:TaxSubtotal", namespaces=NS)
        ],
    }


def values_match(kind: Kind, expected: str | None, actual: str | None) -> bool:
    """Compare two field values the way Phase 4 will score them."""
    if expected is None or actual is None:
        return expected == actual
    if kind in (Kind.AMOUNT, Kind.QUANTITY):
        try:
            return Decimal(expected) == Decimal(actual)
        except (ArithmeticError, ValueError):
            return False
    if kind is Kind.CODE:
        return expected.strip().upper() == actual.strip().upper()
    return " ".join(expected.split()) == " ".join(actual.split())
