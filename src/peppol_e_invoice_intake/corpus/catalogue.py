"""The base invoices the corpus is generated from.

Twenty documents chosen for the situations that actually break extraction and
mapping, not for visual variety - that comes from the layouts. The list leans
deliberately on what the project brief calls hard: several VAT rates on one
document, reverse charge, intra-community supply, discounts, line items that run
past a page break, and arithmetic that sits on a rounding boundary.

Language belongs to the invoice, not to the render. A Dutch-authored invoice
shown with French column headings is a document no supplier would ever send, so
each base invoice is written in one language throughout - item names, notes,
payment terms and exemption reasons included. The split is roughly Belgian:
half Dutch, a third French, the rest English for cross-border trade.

Every party identifier is computed rather than invented, so the whole catalogue
satisfies PEPPOL-COMMON-R043's mod-97 check on 0208 enterprise numbers and
PEPPOL-COMMON-R040's GS1 check on GLNs.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .identifiers import (
    belgian_enterprise_number,
    belgian_iban,
    belgian_vat_number,
    gln,
    structured_payment_reference,
)
from .model import Invoice, InvoiceLine, Language, Party, VatCategory


def _be_party(
    name: str, street: str, postal_zone: str, city: str, base: int, **kwargs
) -> Party:
    number = belgian_enterprise_number(base)
    return Party(
        name=name,
        street=street,
        city=city,
        postal_zone=postal_zone,
        country="BE",
        vat_id=belgian_vat_number(number),
        legal_id=number,
        legal_scheme="0208",
        endpoint_id=number,
        endpoint_scheme="0208",
        **kwargs,
    )


def _foreign_party(
    name: str,
    street: str,
    postal_zone: str,
    city: str,
    country: str,
    vat_id: str | None,
    endpoint_id: str,
    endpoint_scheme: str,
) -> Party:
    """Non-Belgian buyer.

    The electronic address scheme has to match the identifier it carries:
    PEPPOL-COMMON-R040 validates the GS1 check digit on anything routed under
    scheme 0088, so a VAT number cannot simply be labelled a GLN. Each country
    gets the scheme that fits - 9944 for NL VAT, 9930 for DE VAT, a real GLN
    otherwise.
    """
    return Party(
        name=name,
        street=street,
        city=city,
        postal_zone=postal_zone,
        country=country,
        vat_id=vat_id,
        legal_id=None,
        legal_scheme=None,
        endpoint_id=endpoint_id,
        endpoint_scheme=endpoint_scheme,
    )


# --- parties ------------------------------------------------------------------
# Flemish suppliers invoice in Dutch, Walloon suppliers in French, and the
# exporters in English. Matching the party to the language keeps the documents
# plausible rather than merely translated.

HAVENKANTOOR = _be_party(
    "Havenkantoor Logistiek BV", "Noorderlaan 127", "2030", "Antwerpen", 4030405,
    contact_name="Ann Peeters", contact_email="facturatie@havenkantoor.be",
    contact_phone="+32 3 205 44 10",
)
DE_KEYSER = _be_party(
    "De Keyser Bouwmaterialen NV", "Mechelsesteenweg 88", "2800", "Mechelen", 5398015
)
VANDENBERGHE = _be_party(
    "Vandenberghe Interim BVBA", "Kortrijksesteenweg 412", "9000", "Gent", 4561237,
    contact_email="admin@vandenberghe-interim.be",
)
MOENS_ADVIES = _be_party(
    "Moens Fiscaal Advies BV", "Grote Markt 12", "3000", "Leuven", 6612340,
    contact_name="Pieter Moens",
)
NOORDZEE_STAAL = _be_party(
    "Noordzee Staal NV", "Havenlaan 220", "8380", "Zeebrugge", 3344556
)
CLAES_SCHRIJNWERK = _be_party(
    "Claes Schrijnwerkerij BV", "Diestsesteenweg 301", "3010", "Kessel-Lo", 9911223
)
DELHAYE_TRANSPORT = _be_party(
    "Delhaye Transport NV", "Industrieweg 7", "8800", "Roeselare", 8123457
)

LAMBERT_SPRL = _be_party(
    "Lambert Électricité SPRL", "Rue de la Station 45", "4000", "Liège", 7771234,
    contact_name="Gaëtan Lambert", contact_email="facturation@lambert-elec.be",
)
DUBOIS_MATERIAUX = _be_party(
    "Dubois Matériaux SA", "Chaussée de Marche 210", "5100", "Namur", 2233445
)
RENARD_COMPTA = _be_party(
    "Renard Comptabilité SPRL", "Boulevard Tirou 62", "6000", "Charleroi", 5566778,
    contact_email="info@renard-compta.be",
)
THIRY_INTERIM = _be_party(
    "Thiry Intérim SA", "Rue du Commerce 18", "7000", "Mons", 6677889
)

VAN_DIJK_NL = _foreign_party(
    "Van Dijk Groothandel B.V.", "Keizersgracht 210", "1016 DW", "Amsterdam", "NL",
    vat_id="NL812345678B01", endpoint_id="NL812345678B01", endpoint_scheme="9944",
)
MUELLER_DE = _foreign_party(
    "Müller Maschinenbau GmbH", "Industriestraße 14", "40210", "Düsseldorf", "DE",
    vat_id="DE812345678", endpoint_id="DE812345678", endpoint_scheme="9930",
)
ALPINE_CH = _foreign_party(
    "Alpine Precision AG", "Bahnhofstrasse 3", "8001", "Zürich", "CH",
    vat_id=None, endpoint_id=gln(760012345678), endpoint_scheme="0088",
)

IBAN_HAVEN = belgian_iban(539, 75470)
IBAN_NOORDZEE = belgian_iban(310, 123456)
IBAN_LAMBERT = belgian_iban(1, 1)
IBAN_DUBOIS = belgian_iban(275, 884411)

TERMS = {
    Language.NL: "binnen 30 dagen",
    Language.FR: "à 30 jours",
    Language.EN: "within 30 days",
}


def _line(
    line_id: str,
    name: str,
    quantity: str,
    unit_price: str,
    *,
    rate: str = "21",
    unit: str = "C62",
    description: str | None = None,
    discount: str | None = None,
    category: VatCategory = VatCategory.STANDARD,
) -> InvoiceLine:
    return InvoiceLine(
        line_id=line_id,
        name=name,
        description=description,
        quantity=Decimal(quantity),
        unit_code=unit,
        unit_price=Decimal(unit_price),
        vat_rate=Decimal(rate),
        vat_category=category,
        discount=Decimal(discount) if discount is not None else None,
    )


def _many_lines(count: int) -> list[InvoiceLine]:
    """Enough line items to run past a page break in every layout."""
    articles = (
        "Betonblok grijs 39x19x19", "Snelbouwsteen B15", "Cementmortel 25 kg",
        "Isolatieplaat PIR 60 mm", "Dampscherm folie 100 m", "Gipsplaat 12,5 mm",
        "Houten regel 38x63 mm", "Schroeven 4x60 doos 200", "Voegmortel wit 25 kg",
        "Wapeningsnet 5 mm",
    )
    return [
        _line(
            str(index),
            f"{articles[(index - 1) % len(articles)]} (partij {index:02d})",
            quantity=str(4 + (index % 7) * 3),
            unit_price=f"{12 + (index % 11) * 3}.{(index * 7) % 100:02d}",
            rate="21" if index % 5 else "6",
        )
        for index in range(1, count + 1)
    ]


def _rounding_lines() -> list[InvoiceLine]:
    """Prices carrying a third decimal, so quantity x price lands on a half cent.

    This is where per-line and per-group VAT rounding disagree and BR-CO-17
    decides which one is right, and where PEPPOL-EN16931-R120 catches an emitter
    that prints the price rounded to two places.
    """
    return [
        _line("1", "Bloc consultance A", "3", "16.665"),
        _line("2", "Bloc consultance B", "7", "4.285"),
        _line("3", "Licence mensuelle", "11", "9.995"),
        _line("4", "Forfait support", "1", "0.125"),
        _line("5", "Frais d'envoi", "1", "7.505", rate="6"),
    ]


#: Reference prefixes a buyer in each language would actually quote.
REFERENCE_PREFIXES = {
    Language.NL: ("BB", "BEST", "INK"),
    Language.FR: ("BC", "CMD", "REF"),
    Language.EN: ("PO", "ORD", "REQ"),
}


def _reference_for(key: str, language: Language) -> str:
    """A distinct buyer reference per invoice, derived stably from its key.

    One shared reference across the catalogue would let extraction score well on
    BT-10 by memorising a single string, which is exactly the kind of flattery
    the corpus is supposed to avoid.
    """
    digest = sum(ord(character) * (position + 1) for position, character in enumerate(key))
    prefixes = REFERENCE_PREFIXES[language]
    return f"{prefixes[digest % len(prefixes)]}-2026-{digest % 9000 + 1000:04d}"


def _issue_date_for(key: str) -> date:
    """Spread issue dates across the first quarter of 2026.

    A catalogue dated entirely on one day makes BT-2 trivial, and hides the
    localised date formats the templates are there to exercise.
    """
    digest = sum(ord(character) for character in key)
    return date(2026, 1, 5) + timedelta(days=digest % 80)


def _due_date_for(key: str) -> date:
    return _issue_date_for(key) + timedelta(days=30)


def _invoice(
    key: str,
    number: str,
    supplier: Party,
    customer: Party,
    lines: list[InvoiceLine],
    *,
    language: Language = Language.NL,
    **kwargs,
) -> tuple[str, Invoice]:
    defaults = {
        "issue_date": _issue_date_for(key),
        "due_date": _due_date_for(key),
        "buyer_reference": _reference_for(key, language),
        "payment_terms": TERMS[language],
        "iban": IBAN_HAVEN,
    }
    return key, Invoice(
        number=number,
        supplier=supplier,
        customer=customer,
        lines=lines,
        language=language,
        **{**defaults, **kwargs},
    )


CATALOGUE: dict[str, Invoice] = dict(
    (
        # --- Dutch ------------------------------------------------------------
        _invoice(
            "standard-single-rate", "2026-0001", HAVENKANTOOR, DE_KEYSER,
            [
                _line("1", "Palletzending standaard", "10", "100.00",
                      description="Afhaling Antwerpen Noord, levering Mechelen"),
                _line("2", "Opslag per maand", "2", "250.00"),
            ],
            order_reference="BB-88213",
            payment_reference=structured_payment_reference(2026, 1),
        ),
        _invoice(
            "multiple-vat-rates", "2026-0002", HAVENKANTOOR, DE_KEYSER,
            [
                _line("1", "Transport binnenland", "4", "185.00"),
                _line("2", "Maaltijdcheques verwerking", "1", "62.50", rate="6"),
                _line("3", "Sociale huisvesting werken", "1", "940.00", rate="12"),
                _line("4", "Administratiekosten", "1", "45.50", rate="6"),
            ],
            payment_reference=structured_payment_reference(2026, 2),
        ),
        _invoice(
            "line-discounts", "2026-0003", NOORDZEE_STAAL, CLAES_SCHRIJNWERK,
            [
                _line("1", "Staalplaat 2 mm", "24", "87.40", discount="104.88",
                      description="Volumekorting 5%"),
                _line("2", "Kokerprofiel 40x40", "60", "19.95", discount="59.85"),
                _line("3", "Snijwerk op maat", "8", "45.00", discount="18.00"),
            ],
            iban=IBAN_NOORDZEE,
            order_reference="ORD-2026-771",
            payment_reference=structured_payment_reference(2026, 3),
        ),
        _invoice(
            "multi-page-line-items", "2026-0004", DE_KEYSER, CLAES_SCHRIJNWERK,
            _many_lines(42),
            note="Levering in twee zendingen, week 11 en week 12.",
            payment_reference=structured_payment_reference(2026, 4),
        ),
        _invoice(
            "partially-prepaid", "2026-0005", HAVENKANTOOR, DE_KEYSER,
            [_line("1", "Jaarcontract opslag 2026", "1", "9600.00")],
            prepaid_amount=Decimal("4000.00"),
            note="Voorschot gefactureerd op 15 januari 2026, factuur 2026-0001-V.",
            payment_reference=structured_payment_reference(2026, 5),
        ),
        _invoice(
            "zero-rated-supply", "2026-0006", DE_KEYSER, HAVENKANTOOR,
            [
                _line("1", "Dagbladen abonnement kwartaal", "12", "38.50",
                      rate="0", category=VatCategory.ZERO_RATED),
            ],
            # BR-Z-10: a zero-rated breakdown must NOT carry an exemption reason.
            payment_reference=structured_payment_reference(2026, 6),
        ),
        _invoice(
            "mixed-units-of-measure", "2026-0007", NOORDZEE_STAAL, CLAES_SCHRIJNWERK,
            [
                _line("1", "Plaatstaal gegalvaniseerd", "1450.75", "2.35", unit="KGM"),
                _line("2", "Kantlijn profiel", "88", "12.10", unit="MTR"),
                _line("3", "Ontvettingsmiddel", "40", "6.75", unit="LTR"),
                _line("4", "Montage-uren", "16", "52.00", unit="HUR"),
            ],
            iban=IBAN_NOORDZEE,
            payment_reference=structured_payment_reference(2026, 7),
        ),
        _invoice(
            "small-amounts", "2026-0008", DE_KEYSER, CLAES_SCHRIJNWERK,
            [
                _line("1", "Schroef RVS M4", "250", "0.07"),
                _line("2", "Sluitring M4", "500", "0.03"),
                _line("3", "Moer M4", "250", "0.05"),
                _line("4", "Zakje assortiment", "3", "1.95", rate="6"),
            ],
            payment_reference=structured_payment_reference(2026, 8),
        ),
        _invoice(
            "contractual-discount", "2026-0009", HAVENKANTOOR, DELHAYE_TRANSPORT,
            [
                _line("1", "Opslagvergoeding Q1", "1", "3200.00", discount="480.00",
                      description="Contractuele korting 15%"),
                _line("2", "Handling in/uit", "640", "1.85", discount="118.40"),
                _line("3", "Documentbehandeling", "1", "95.00", rate="6"),
            ],
            order_reference="RAAM-2026-LOG-014",
            payment_reference=structured_payment_reference(2026, 9),
        ),
        _invoice(
            "minimal-single-line", "2026-0010", MOENS_ADVIES, DELHAYE_TRANSPORT,
            [_line("1", "Advies dossier 2026-11", "1", "450.00")],
            due_date=None,
            iban=None,
            payment_reference=None,
            # BR-CO-25: a positive amount due needs either a due date or terms.
            # This invoice drops the due date, so the terms have to stay.
            payment_terms="contant bij ontvangst",
            buyer_reference="DOSSIER-2026-11",
        ),
        # --- French -----------------------------------------------------------
        _invoice(
            "reverse-charge-construction", "2026-0011", LAMBERT_SPRL, DUBOIS_MATERIAUX,
            [
                _line("1", "Installation électrique neuve", "1", "14750.00",
                      rate="0", category=VatCategory.REVERSE_CHARGE),
                _line("2", "Tableau de répartition et câblage", "1", "3280.00",
                      rate="0", category=VatCategory.REVERSE_CHARGE),
            ],
            language=Language.FR,
            exemption_reason=(
                "Autoliquidation - cocontractant, article 20 de l'AR n° 1"
            ),
            iban=IBAN_LAMBERT,
            payment_reference=structured_payment_reference(2026, 11),
        ),
        _invoice(
            "rounding-boundaries", "2026-0012", RENARD_COMPTA, THIRY_INTERIM,
            _rounding_lines(),
            language=Language.FR,
            iban=IBAN_DUBOIS,
            payment_reference=structured_payment_reference(2026, 12),
        ),
        _invoice(
            "exempt-services", "2026-0013", RENARD_COMPTA, THIRY_INTERIM,
            [
                _line("1", "Courtage contrat d'assurance", "1", "1250.00",
                      rate="0", category=VatCategory.EXEMPT),
            ],
            language=Language.FR,
            exemption_reason="Exonéré de TVA, article 44 du Code de la TVA",
            iban=IBAN_DUBOIS,
            payment_reference=structured_payment_reference(2026, 13),
        ),
        _invoice(
            "fractional-quantities", "2026-0014", THIRY_INTERIM, LAMBERT_SPRL,
            [
                _line("1", "Intérimaire électricien", "37.5", "48.90", unit="HUR",
                      description="Semaine 9, équipe A"),
                _line("2", "Intérimaire manoeuvre", "22.25", "31.40", unit="HUR"),
                _line("3", "Suivi administratif", "1.5", "65.00", unit="HUR"),
            ],
            language=Language.FR,
            iban=IBAN_DUBOIS,
            payment_reference=structured_payment_reference(2026, 14),
        ),
        _invoice(
            "four-vat-rates", "2026-0015", DUBOIS_MATERIAUX, THIRY_INTERIM,
            [
                _line("1", "Matériaux divers", "10", "120.00", rate="21"),
                _line("2", "Rénovation habitation de plus de 10 ans", "1", "2400.00", rate="6"),
                _line("3", "Logement social", "1", "1800.00", rate="12"),
                _line("4", "Avances refacturées", "1", "310.00",
                      rate="0", category=VatCategory.EXEMPT),
            ],
            language=Language.FR,
            exemption_reason="Exonéré de TVA, article 44 du Code de la TVA",
            iban=IBAN_DUBOIS,
            payment_reference=structured_payment_reference(2026, 15),
        ),
        _invoice(
            "delivery-and-order-references", "2026-0016", DUBOIS_MATERIAUX, LAMBERT_SPRL,
            [
                _line("1", "Trajet Namur - Liège", "6", "285.00",
                      description="Trajets 2026/0311 à 2026/0316"),
                _line("2", "Heures d'attente", "3.5", "62.00", unit="HUR"),
                _line("3", "Péage refacturé", "1", "148.20"),
            ],
            language=Language.FR,
            delivery_date=date(2026, 2, 28),
            order_reference="TRANS-2026-0311",
            buyer_reference="LOG/2026/T1",
            note="Lettres de voiture disponibles sur demande.",
            iban=IBAN_DUBOIS,
            payment_reference=structured_payment_reference(2026, 16),
        ),
        # --- English ----------------------------------------------------------
        _invoice(
            "intra-community-supply", "2026-0017", NOORDZEE_STAAL, VAN_DIJK_NL,
            [
                _line("1", "Steel profile HEA 200", "18", "210.00", rate="0",
                      category=VatCategory.INTRA_COMMUNITY),
                _line("2", "Carriage to Amsterdam", "1", "480.00", rate="0",
                      category=VatCategory.INTRA_COMMUNITY),
            ],
            language=Language.EN,
            exemption_reason=(
                "Intra-community supply, exempt under article 39bis of the Belgian VAT Code"
            ),
            delivery_date=date(2026, 2, 26),
            delivery_country="NL",
            iban=IBAN_NOORDZEE,
            buyer_reference="NL-PO-2291",
            payment_reference=structured_payment_reference(2026, 17),
        ),
        _invoice(
            "export-outside-eu", "2026-0018", NOORDZEE_STAAL, ALPINE_CH,
            [
                _line("1", "Precision components series C", "120", "44.75", rate="0",
                      category=VatCategory.EXPORT),
            ],
            language=Language.EN,
            exemption_reason=(
                "Export outside the EU, exempt under article 39 of the Belgian VAT Code"
            ),
            delivery_date=date(2026, 2, 20),
            delivery_country="CH",
            iban=IBAN_NOORDZEE,
            buyer_reference="CH-PO-4410",
            payment_reference=structured_payment_reference(2026, 18),
        ),
        _invoice(
            "large-amounts", "2026-0019", NOORDZEE_STAAL, MUELLER_DE,
            [
                _line("1", "Steel structure hall 2", "1", "486750.00", rate="0",
                      category=VatCategory.INTRA_COMMUNITY),
                _line("2", "Assembly and transport", "1", "97430.50", rate="0",
                      category=VatCategory.INTRA_COMMUNITY),
            ],
            language=Language.EN,
            exemption_reason=(
                "Intra-community supply, exempt under article 39bis of the Belgian VAT Code"
            ),
            delivery_date=date(2026, 2, 12),
            delivery_country="DE",
            iban=IBAN_NOORDZEE,
            buyer_reference="DE-BST-99120",
            payment_reference=structured_payment_reference(2026, 19),
        ),
        _invoice(
            "long-descriptions", "2026-0020", CLAES_SCHRIJNWERK, VAN_DIJK_NL,
            [
                _line(
                    "1", "Bespoke meeting room cabinet wall", "1", "6850.00",
                    rate="0", category=VatCategory.INTRA_COMMUNITY,
                    description=(
                        "Supply and installation of a fully bespoke cabinet wall in oak "
                        "veneer, comprising three soft-close sliding doors, integrated LED "
                        "lighting, cable entries for AV equipment, and a removable panel "
                        "giving access to the technical shaft behind the wall."
                    ),
                ),
                _line(
                    "2", "Installation labour and finishing", "24", "58.00", unit="HUR",
                    rate="0", category=VatCategory.INTRA_COMMUNITY,
                    description=(
                        "Includes dismantling the existing furniture, removal and "
                        "processing of waste in line with VLAREMA, and making good the "
                        "surrounding paintwork after installation."
                    ),
                ),
            ],
            language=Language.EN,
            exemption_reason=(
                "Intra-community supply, exempt under article 39bis of the Belgian VAT Code"
            ),
            delivery_date=date(2026, 2, 24),
            delivery_country="NL",
            buyer_reference="NL-PO-2318",
            payment_reference=structured_payment_reference(2026, 20),
        ),
    )
)

assert len(CATALOGUE) == 20, f"catalogue should hold 20 base invoices, has {len(CATALOGUE)}"
