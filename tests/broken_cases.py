"""Deliberately-broken variants of the valid base invoice.

Each case applies exactly one mutation and pins the rule IDs it is expected to
trip. Variants are generated from the valid base rather than committed as
separate XML files, so a change to the base cannot silently drift away from its
broken siblings.

`expected_rules` is pinned to OBSERVED validator behaviour (captured via
scripts/show_rules.py), not to what the rule text was assumed to say. A case that
starts tripping a different rule fails the suite just as loudly as one that stops
failing at all - that is the point of the harness.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

FIXTURES = Path(__file__).parent / "fixtures"
VALID_BASE = FIXTURES / "valid" / "be-standard-vat.xml"

INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NS = {"inv": INV, "cac": CAC, "cbc": CBC}


def load_base() -> etree._ElementTree:
    return etree.parse(str(VALID_BASE))


def _find(tree: etree._ElementTree, xpath: str) -> etree._Element:
    matches = tree.xpath(xpath, namespaces=NS)
    if not matches:
        raise AssertionError(f"Fixture mutation targets a missing node: {xpath}")
    return matches[0]


def drop(xpath: str) -> Callable[[etree._ElementTree], None]:
    """Remove the first node matching xpath."""

    def mutate(tree: etree._ElementTree) -> None:
        node = _find(tree, xpath)
        node.getparent().remove(node)

    return mutate


def set_text(xpath: str, value: str) -> Callable[[etree._ElementTree], None]:
    def mutate(tree: etree._ElementTree) -> None:
        _find(tree, xpath).text = value

    return mutate


def set_attr(xpath: str, name: str, value: str) -> Callable[[etree._ElementTree], None]:
    def mutate(tree: etree._ElementTree) -> None:
        _find(tree, xpath).set(name, value)

    return mutate


@dataclass(frozen=True)
class BrokenCase:
    id: str
    description: str
    mutate: Callable[[etree._ElementTree], None]
    expected_rules: frozenset[str]

    def build(self) -> bytes:
        tree = load_base()
        self.mutate(tree)
        return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.xml"
        path.write_bytes(self.build())
        return path


CASES: tuple[BrokenCase, ...] = (
    BrokenCase(
        "missing-customization-id",
        "CustomizationID removed - document no longer declares which spec it follows",
        drop("//cbc:CustomizationID"),
        frozenset({"BR-01", "PEPPOL-EN16931-R004"}),
    ),
    BrokenCase(
        "missing-profile-id",
        "ProfileID removed",
        drop("//cbc:ProfileID"),
        frozenset({"PEPPOL-EN16931-R001", "PEPPOL-EN16931-R007"}),
    ),
    BrokenCase(
        "missing-seller-endpoint",
        "Seller EndpointID removed - receiver cannot be routed on the network",
        drop("//cac:AccountingSupplierParty//cbc:EndpointID"),
        frozenset({"PEPPOL-EN16931-R020"}),
    ),
    BrokenCase(
        "missing-buyer-endpoint",
        "Buyer EndpointID removed",
        drop("//cac:AccountingCustomerParty//cbc:EndpointID"),
        frozenset({"PEPPOL-EN16931-R010"}),
    ),
    BrokenCase(
        "missing-buyer-reference",
        "BuyerReference removed and no OrderReference present",
        drop("//cbc:BuyerReference"),
        frozenset({"PEPPOL-EN16931-R003"}),
    ),
    BrokenCase(
        "tax-total-mismatch",
        "Document TaxAmount no longer equals the sum of its subtotals",
        set_text("//cac:TaxTotal/cbc:TaxAmount", "310.00"),
        frozenset({"BR-CO-14", "BR-CO-15"}),
    ),
    BrokenCase(
        "tax-subtotal-miscalculated",
        "Subtotal VAT amount does not match taxable amount x rate",
        set_text("//cac:TaxSubtotal/cbc:TaxAmount", "300.00"),
        frozenset({"BR-CO-14", "BR-CO-17", "BR-S-09"}),
    ),
    BrokenCase(
        "line-sum-mismatch",
        "LineExtensionAmount total does not equal the sum of invoice lines",
        set_text("//cac:LegalMonetaryTotal/cbc:LineExtensionAmount", "1400.00"),
        frozenset({"BR-CO-10", "BR-CO-13"}),
    ),
    BrokenCase(
        "payable-amount-mismatch",
        "PayableAmount does not equal TaxInclusiveAmount",
        set_text("//cac:LegalMonetaryTotal/cbc:PayableAmount", "1800.00"),
        frozenset({"BR-CO-16"}),
    ),
    BrokenCase(
        "invalid-country-code",
        "Seller country is not a valid ISO 3166-1 alpha-2 code",
        set_text("//cac:AccountingSupplierParty//cac:Country/cbc:IdentificationCode", "XX"),
        frozenset({"BR-CL-14"}),
    ),
    BrokenCase(
        "invalid-currency-code",
        "DocumentCurrencyCode is not a valid ISO 4217 code",
        set_text("//cbc:DocumentCurrencyCode", "XYZ"),
        frozenset({"BR-CL-04", "BR-CO-15", "PEPPOL-EN16931-R051"}),
    ),
    BrokenCase(
        "bad-belgian-enterprise-number",
        "0208 enterprise number fails the mod-97 check digit test",
        set_text("//cac:AccountingSupplierParty//cac:PartyLegalEntity/cbc:CompanyID", "0403040500"),
        frozenset({"PEPPOL-COMMON-R043"}),
    ),
    BrokenCase(
        "invalid-invoice-type-code",
        "InvoiceTypeCode is not in the Peppol-permitted subset",
        set_text("//cbc:InvoiceTypeCode", "999"),
        frozenset({"BR-CL-01", "PEPPOL-EN16931-P0100"}),
    ),
    BrokenCase(
        "missing-line-item-name",
        "Invoice line item has no name",
        drop("(//cac:InvoiceLine)[1]//cac:Item/cbc:Name"),
        frozenset({"BR-25"}),
    ),
)

CASES_BY_ID = {case.id: case for case in CASES}
