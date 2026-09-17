"""Labels and number/date formatting per language.

The PDF renders Belgian conventions - 1.234,56 and 02/03/2026 - while the UBL
carries 1234.56 and 2026-03-02. That gap is deliberate: normalising a localised
date and a comma decimal separator back to the canonical form is exactly the kind
of thing Phase 3 has to get right, and Phase 4 measures. A corpus that rendered
ISO dates would flatter the pipeline.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .model import Language, VatCategory

LABELS: dict[Language, dict[str, str]] = {
    Language.NL: {
        "invoice": "FACTUUR",
        "invoice_number": "Factuurnummer",
        "issue_date": "Factuurdatum",
        "due_date": "Vervaldatum",
        "delivery_date": "Leveringsdatum",
        "buyer_reference": "Uw referentie",
        "order_reference": "Bestelbon",
        "supplier": "Leverancier",
        "customer": "Klant",
        "bill_to": "Factuuradres",
        "vat_number": "BTW-nummer",
        "enterprise_number": "Ondernemingsnummer",
        "line": "Nr",
        "description": "Omschrijving",
        "quantity": "Aantal",
        "unit_price": "Eenheidsprijs",
        "discount": "Korting",
        "vat_rate": "BTW",
        "line_total": "Totaal",
        "subtotal": "Subtotaal excl. BTW",
        "vat_breakdown": "BTW-overzicht",
        "taxable": "Maatstaf",
        "vat_amount": "BTW-bedrag",
        "total_excl": "Totaal excl. BTW",
        "total_vat": "Totaal BTW",
        "total_incl": "Totaal incl. BTW",
        "prepaid": "Reeds betaald",
        "payable": "TE BETALEN",
        "iban": "IBAN",
        "payment_reference": "Gestructureerde mededeling",
        "payment_terms": "Betalingsvoorwaarden",
        "page": "Pagina",
        "of": "van",
        "continued": "vervolg",
    },
    Language.FR: {
        "invoice": "FACTURE",
        "invoice_number": "Numéro de facture",
        "issue_date": "Date de facture",
        "due_date": "Échéance",
        "delivery_date": "Date de livraison",
        "buyer_reference": "Votre référence",
        "order_reference": "Bon de commande",
        "supplier": "Fournisseur",
        "customer": "Client",
        "bill_to": "Adresse de facturation",
        "vat_number": "Numéro de TVA",
        "enterprise_number": "Numéro d'entreprise",
        "line": "N°",
        "description": "Désignation",
        "quantity": "Quantité",
        "unit_price": "Prix unitaire",
        "discount": "Remise",
        "vat_rate": "TVA",
        "line_total": "Total",
        "subtotal": "Sous-total HTVA",
        "vat_breakdown": "Récapitulatif TVA",
        "taxable": "Base",
        "vat_amount": "Montant TVA",
        "total_excl": "Total HTVA",
        "total_vat": "Total TVA",
        "total_incl": "Total TVAC",
        "prepaid": "Déjà payé",
        "payable": "À PAYER",
        "iban": "IBAN",
        "payment_reference": "Communication structurée",
        "payment_terms": "Conditions de paiement",
        "page": "Page",
        "of": "sur",
        "continued": "suite",
    },
    Language.EN: {
        "invoice": "INVOICE",
        "invoice_number": "Invoice number",
        "issue_date": "Invoice date",
        "due_date": "Due date",
        "delivery_date": "Delivery date",
        "buyer_reference": "Your reference",
        "order_reference": "Purchase order",
        "supplier": "Supplier",
        "customer": "Customer",
        "bill_to": "Bill to",
        "vat_number": "VAT number",
        "enterprise_number": "Company number",
        "line": "No",
        "description": "Description",
        "quantity": "Qty",
        "unit_price": "Unit price",
        "discount": "Discount",
        "vat_rate": "VAT",
        "line_total": "Amount",
        "subtotal": "Subtotal excl. VAT",
        "vat_breakdown": "VAT summary",
        "taxable": "Taxable",
        "vat_amount": "VAT amount",
        "total_excl": "Total excl. VAT",
        "total_vat": "Total VAT",
        "total_incl": "Total incl. VAT",
        "prepaid": "Already paid",
        "payable": "AMOUNT DUE",
        "iban": "IBAN",
        "payment_reference": "Payment reference",
        "payment_terms": "Payment terms",
        "page": "Page",
        "of": "of",
        "continued": "continued",
    },
}

#: Wording a Belgian supplier would actually print for a non-standard VAT category.
VAT_CATEGORY_NOTE: dict[Language, dict[VatCategory, str]] = {
    Language.NL: {
        VatCategory.REVERSE_CHARGE: "BTW verlegd",
        VatCategory.INTRA_COMMUNITY: "Intracommunautaire levering",
        VatCategory.EXEMPT: "Vrijgesteld van BTW",
        VatCategory.ZERO_RATED: "Nultarief",
        VatCategory.EXPORT: "Uitvoer buiten de EU",
    },
    Language.FR: {
        VatCategory.REVERSE_CHARGE: "Autoliquidation",
        VatCategory.INTRA_COMMUNITY: "Livraison intracommunautaire",
        VatCategory.EXEMPT: "Exonéré de TVA",
        VatCategory.ZERO_RATED: "Taux zéro",
        VatCategory.EXPORT: "Exportation hors UE",
    },
    Language.EN: {
        VatCategory.REVERSE_CHARGE: "Reverse charge",
        VatCategory.INTRA_COMMUNITY: "Intra-community supply",
        VatCategory.EXEMPT: "VAT exempt",
        VatCategory.ZERO_RATED: "Zero rated",
        VatCategory.EXPORT: "Export outside the EU",
    },
}

MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def labels(language: Language) -> dict[str, str]:
    return LABELS[language]


def format_date(value: date, language: Language) -> str:
    """Belgian dd/mm/yyyy for NL and FR; a spelled-out month for EN."""
    if language is Language.EN:
        return f"{value.day} {MONTHS_EN[value.month - 1]} {value.year}"
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


def format_amount(value: Decimal, language: Language) -> str:
    """1.234,56 for NL and FR; 1,234.56 for EN."""
    formatted = f"{value:,.2f}"
    if language is Language.EN:
        return formatted
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_quantity(value: Decimal, language: Language) -> str:
    normalised = value.normalize()
    text = f"{normalised:f}"
    if language is Language.EN or "." not in text:
        return text
    return text.replace(".", ",")


def format_rate(value: Decimal, language: Language) -> str:
    return f"{format_quantity(value, language)}%"
