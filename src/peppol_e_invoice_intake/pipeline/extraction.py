"""Read a supplier invoice PDF into `ExtractedInvoice`.

Two arms, so the cost/accuracy tradeoff can be measured rather than asserted:

* ``vision`` sends the PDF itself, so the model sees layout, columns and rules.
* ``text``   sends only the PDF's text layer, which is far cheaper and loses
  the spatial information that tells a quantity from a unit price.

The arm is a parameter rather than a fork in the code so phase 4 can report both
numbers side by side. A scanned invoice has no text layer at all, which the text
arm reports as a failure rather than hallucinating around.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from .budget import DEFAULT_MODEL, Budget, Spend
from .request import ModelRequestError, structured_request
from .schema import ExtractedInvoice

if TYPE_CHECKING:
    import anthropic


class Arm(StrEnum):
    VISION = "vision"
    TEXT = "text"


SYSTEM_PROMPT = """\
You read supplier invoices and report exactly what is printed on them.

You are working with Belgian and other European invoices. They are written in \
Dutch, French or English, and they follow continental conventions that differ \
from the canonical form you must return:

- Amounts print as 1.234,56 - a dot groups thousands and a comma is the decimal \
separator. Return 1234.56, with a dot as the decimal separator and no grouping.
- Dates print as DD/MM/YYYY. 02/03/2026 is 2 March 2026, so return 2026-03-02. \
English invoices may spell the month out. Never return a date you had to guess \
the order of; record an uncertainty instead.
- A VAT rate of 0% does not tell you the VAT category on its own. Read the \
wording near the line or in the VAT summary: "btw verlegd", "medecontractant", \
"autoliquidation" and "reverse charge" mean AE; "intracommunautaire levering", \
"livraison intracommunautaire" and "intra-community supply" mean K; "uitvoer", \
"exportation" and "export" mean G; "vrijgesteld" and "exonere" mean E; an \
explicit zero rate with no exemption wording means Z.

Rules you must follow:

1. Report what is printed. Do not calculate anything. If a total is not printed \
on the document, return null for it rather than working it out from the lines.
2. Transcribe every invoice line, in the order printed, including lines that \
continue onto a second page. Do not merge, reorder or summarise them.
3. A line's unit price is the price before VAT for one unit. Where a line shows \
a discount, report the discount separately as a positive amount and leave the \
unit price as the undiscounted one.
4. Prefer null and an entry in `uncertainties` over a guess. A field you record \
as uncertain can be checked by a person; a field you guessed wrong cannot.
5. Identifiers must be copied character for character - VAT numbers, enterprise \
numbers, IBANs and structured payment references. Do not reformat, space or \
tidy them.

The column layout varies between suppliers. Some invoices put the amount column \
first and the description last. Read the column headings rather than assuming a \
position.\
"""

VISION_INSTRUCTION = (
    "Read this invoice and return the structured extraction. "
    "The document is attached as a PDF."
)

TEXT_INSTRUCTION = """\
Read this invoice and return the structured extraction.

Only the text layer of the PDF is available, not the visual layout. Columns have \
been flattened into reading order, so figures belonging to one row may appear \
consecutively without their headings. Where the flattening makes a value \
genuinely ambiguous, record an uncertainty rather than guessing.

--- begin extracted text ---
{text}
--- end extracted text ---\
"""


#: Effort for the first reading. Reading an invoice is transcription, not
#: reasoning, so it runs at the cheapest setting; the documents it gets wrong are
#: caught by validation or reconciliation and re-read at REPAIR_EFFORT. This is
#: the "run cheap, re-run failures at the default" pattern, and it only works
#: because the pipeline has a reliable failure signal.
EXTRACT_EFFORT = "low"

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


class ExtractionError(ModelRequestError):
    """Raised when a document could not be extracted at all."""


@dataclass(frozen=True)
class ExtractionResult:
    document: Path
    arm: Arm
    model: str
    invoice: ExtractedInvoice
    spend: Spend
    latency_ms: int


def pdf_text(document: Path) -> str:
    """Extract the text layer, turning any reader failure into an ExtractionError.

    A corrupt or non-PDF file is a normal thing for an intake pipeline to be
    handed. It belongs in the report as an unreadable document, not raised as a
    pypdf internal error from three frames down.
    """
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(str(document))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise ExtractionError(f"{document.name} could not be read as a PDF: {exc}") from exc


def _vision_content(document: Path) -> list[dict]:
    encoded = base64.standard_b64encode(document.read_bytes()).decode("ascii")
    return [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": encoded,
            },
        },
        {"type": "text", "text": VISION_INSTRUCTION},
    ]


def _text_content(document: Path) -> list[dict]:
    text = pdf_text(document).strip()
    if not text:
        raise ExtractionError(
            f"{document.name} has no text layer. The text arm cannot read a scanned "
            "document; use the vision arm."
        )
    return [{"type": "text", "text": TEXT_INSTRUCTION.format(text=text)}]


def build_content(document: Path, arm: Arm) -> list[dict]:
    return _vision_content(document) if arm is Arm.VISION else _text_content(document)


def extract(
    document: Path,
    *,
    arm: Arm | str = Arm.VISION,
    budget: Budget,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    effort: str | None = EXTRACT_EFFORT,
) -> ExtractionResult:
    """Read one invoice PDF. Costs money; the budget is checked first."""
    arm = Arm(arm)

    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    response = structured_request(
        client,
        model=model,
        system=SYSTEM_PROMPT,
        content=build_content(document, arm),
        output_format=ExtractedInvoice,
        budget=budget,
        about=f"extract {document.name}",
        effort=effort,
    )

    return ExtractionResult(
        document=document,
        arm=arm,
        model=model,
        invoice=response.parsed,
        spend=response.spend,
        latency_ms=response.latency_ms,
    )
