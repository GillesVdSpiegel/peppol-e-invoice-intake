"""Emit Peppol BIS Billing 3.0 UBL from an `Invoice`.

UBL element order is significant - the XSD declares sequences, not choices - so
this module builds elements in schema order rather than in whatever order reads
nicely. Anything generated here is expected to pass the Phase 1 validator; the
corpus builder treats a validation failure as a build failure rather than
shipping a fixture that only looks correct.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

from .model import Invoice, InvoiceLine, Party, TaxSubtotal, VatCategory

INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NSMAP = {None: INV, "cac": CAC, "cbc": CBC}

CUSTOMIZATION_ID = (
    "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
)
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
INVOICE_TYPE_CODE = "380"


def _cbc(parent: etree._Element, name: str, text: str | None = None, **attrs) -> etree._Element:
    element = etree.SubElement(parent, f"{{{CBC}}}{name}")
    if text is not None:
        element.text = text
    for key, value in attrs.items():
        element.set(key, value)
    return element


def _cac(parent: etree._Element, name: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{CAC}}}{name}")


def _amount(parent: etree._Element, name: str, value: Decimal, currency: str) -> etree._Element:
    return _cbc(parent, name, f"{value:.2f}", currencyID=currency)


def _rate(value: Decimal) -> str:
    """VAT percentages render without trailing zeros where possible (21, not 21.00)."""
    normalised = value.normalize()
    return f"{normalised:f}"


def _party(parent: etree._Element, party: Party, role: str) -> None:
    wrapper = _cac(parent, role)
    node = _cac(wrapper, "Party")

    _cbc(node, "EndpointID", party.endpoint_id, schemeID=party.endpoint_scheme)

    name_node = _cac(node, "PartyName")
    _cbc(name_node, "Name", party.name)

    address = _cac(node, "PostalAddress")
    _cbc(address, "StreetName", party.street)
    _cbc(address, "CityName", party.city)
    _cbc(address, "PostalZone", party.postal_zone)
    country = _cac(address, "Country")
    _cbc(country, "IdentificationCode", party.country)

    if party.vat_id:
        tax_scheme_node = _cac(node, "PartyTaxScheme")
        _cbc(tax_scheme_node, "CompanyID", party.vat_id)
        scheme = _cac(tax_scheme_node, "TaxScheme")
        _cbc(scheme, "ID", "VAT")

    legal = _cac(node, "PartyLegalEntity")
    _cbc(legal, "RegistrationName", party.registration_name)
    if party.legal_id:
        attrs = {"schemeID": party.legal_scheme} if party.legal_scheme else {}
        _cbc(legal, "CompanyID", party.legal_id, **attrs)

    if party.contact_name or party.contact_email or party.contact_phone:
        contact = _cac(node, "Contact")
        if party.contact_name:
            _cbc(contact, "Name", party.contact_name)
        if party.contact_phone:
            _cbc(contact, "Telephone", party.contact_phone)
        if party.contact_email:
            _cbc(contact, "ElectronicMail", party.contact_email)


def _tax_category(parent: etree._Element, subtotal: TaxSubtotal) -> None:
    category = _cac(parent, "TaxCategory")
    _cbc(category, "ID", subtotal.category.value)
    _cbc(category, "Percent", _rate(subtotal.rate))
    if subtotal.exemption_reason:
        _cbc(category, "TaxExemptionReason", subtotal.exemption_reason)
    scheme = _cac(category, "TaxScheme")
    _cbc(scheme, "ID", "VAT")


def _line(parent: etree._Element, line: InvoiceLine, currency: str) -> None:
    node = _cac(parent, "InvoiceLine")
    _cbc(node, "ID", line.line_id)
    _cbc(node, "InvoicedQuantity", f"{line.quantity:f}", unitCode=line.unit_code)
    _amount(node, "LineExtensionAmount", line.net_amount, currency)

    if line.discount is not None:
        allowance = _cac(node, "AllowanceCharge")
        _cbc(allowance, "ChargeIndicator", "false")
        _cbc(allowance, "AllowanceChargeReason", "Discount")
        _amount(allowance, "Amount", line.discount, currency)

    item = _cac(node, "Item")
    if line.description:
        _cbc(item, "Description", line.description)
    _cbc(item, "Name", line.name)
    classified = _cac(item, "ClassifiedTaxCategory")
    _cbc(classified, "ID", line.vat_category.value)
    _cbc(classified, "Percent", _rate(line.vat_rate))
    scheme = _cac(classified, "TaxScheme")
    _cbc(scheme, "ID", "VAT")

    price = _cac(node, "Price")
    _amount(price, "PriceAmount", line.unit_price, currency)


def build_tree(invoice: Invoice) -> etree._ElementTree:
    root = etree.Element(f"{{{INV}}}Invoice", nsmap=NSMAP)
    currency = invoice.currency

    _cbc(root, "CustomizationID", CUSTOMIZATION_ID)
    _cbc(root, "ProfileID", PROFILE_ID)
    _cbc(root, "ID", invoice.number)
    _cbc(root, "IssueDate", invoice.issue_date.isoformat())
    if invoice.due_date:
        _cbc(root, "DueDate", invoice.due_date.isoformat())
    _cbc(root, "InvoiceTypeCode", INVOICE_TYPE_CODE)
    if invoice.note:
        _cbc(root, "Note", invoice.note)
    _cbc(root, "DocumentCurrencyCode", currency)
    if invoice.buyer_reference:
        _cbc(root, "BuyerReference", invoice.buyer_reference)

    if invoice.order_reference:
        order = _cac(root, "OrderReference")
        _cbc(order, "ID", invoice.order_reference)

    _party(root, invoice.supplier, "AccountingSupplierParty")
    _party(root, invoice.customer, "AccountingCustomerParty")

    # BR-IC-11 requires an actual delivery date on intra-community supplies.
    if invoice.delivery_date:
        delivery = _cac(root, "Delivery")
        _cbc(delivery, "ActualDeliveryDate", invoice.delivery_date.isoformat())

    if invoice.iban or invoice.payment_reference:
        means = _cac(root, "PaymentMeans")
        _cbc(means, "PaymentMeansCode", invoice.payment_means_code)
        if invoice.payment_reference:
            _cbc(means, "PaymentID", invoice.payment_reference)
        if invoice.iban:
            account = _cac(means, "PayeeFinancialAccount")
            _cbc(account, "ID", invoice.iban)

    if invoice.payment_terms:
        terms = _cac(root, "PaymentTerms")
        _cbc(terms, "Note", invoice.payment_terms)

    tax_total = _cac(root, "TaxTotal")
    _amount(tax_total, "TaxAmount", invoice.tax_amount, currency)
    for subtotal in invoice.tax_subtotals:
        node = _cac(tax_total, "TaxSubtotal")
        _amount(node, "TaxableAmount", subtotal.taxable_amount, currency)
        _amount(node, "TaxAmount", subtotal.tax_amount, currency)
        _tax_category(node, subtotal)

    totals = _cac(root, "LegalMonetaryTotal")
    _amount(totals, "LineExtensionAmount", invoice.line_extension_amount, currency)
    _amount(totals, "TaxExclusiveAmount", invoice.tax_exclusive_amount, currency)
    _amount(totals, "TaxInclusiveAmount", invoice.tax_inclusive_amount, currency)
    if invoice.prepaid_amount:
        _amount(totals, "PrepaidAmount", invoice.prepaid_amount, currency)
    _amount(totals, "PayableAmount", invoice.payable_amount, currency)

    for line in invoice.lines:
        _line(root, line, currency)

    return etree.ElementTree(root)


def to_xml(invoice: Invoice) -> bytes:
    return etree.tostring(
        build_tree(invoice), xml_declaration=True, encoding="UTF-8", pretty_print=True
    )


__all__ = ["CUSTOMIZATION_ID", "PROFILE_ID", "VatCategory", "build_tree", "to_xml"]
