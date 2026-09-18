"""One repair attempt, and only one.

Four decisions worth stating, because each could reasonably have gone the other
way:

**The cap is one attempt, not a loop.** An unbounded retry loop converges on
something that validates, which is not the same as something that is right - the
cheapest way to satisfy a rule is often to drop the offending field. One attempt
keeps cost predictable and keeps "passed after repair" a meaningful number rather
than a measure of how long we were willing to wait.

**It is triggered by reconciliation as well as validation.** Totals are computed,
so validation almost never catches a misread line - the emitted document is
internally consistent whatever was misread. A disagreement between the lines and
the totals printed on the page is the stronger, more specific signal, so it earns
a repair too. See docs/decisions/0002-extract-then-compute.md.

**It runs at a higher effort than the first reading.** The first pass is cheap
and most documents should pass it; only the ones with a concrete failure signal
pay for a careful second look.

**Repair edits the extraction, never the XML**, and the model is shown described
problems rather than raw validator output. An SVRL report is a machine artefact
full of XPath; what helps is the business term, what the rule requires, and where
to look on the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..validation import ValidationFinding, ValidationResult
from .budget import DEFAULT_MODEL, Budget, Spend
from .extraction import Arm, build_content
from .mapping import Problem, ProblemKind
from .request import structured_request
from .schema import ExtractedInvoice

#: The repair is the careful read, so it runs at the model's default effort.
REPAIR_EFFORT = "high"

REPAIR_SYSTEM_PROMPT = """\
You are correcting an earlier reading of a supplier invoice.

A first pass extracted the fields below. That reading was then checked two ways: \
the Peppol BIS Billing 3.0 document built from it was validated against the \
official EN 16931 and Peppol rule sets, and its line items were added up and \
compared with the totals printed on the document. It failed at least one of those \
checks. You are being given the problems and one chance to re-read the document \
and return a corrected extraction.

How to approach this:

- If the lines do not add up to the printed totals, one of the lines was misread: \
a quantity, a unit price, a discount, or a VAT rate. Find that line. Do not \
change a printed total to make it agree with the lines - the printed totals are \
the reference, and moving them would hide the mistake rather than fix it.
- If a total-related validation rule failed, the cause is likewise a line, not a \
total. Totals in the output document are recomputed from the lines by code that \
is known to satisfy the arithmetic rules.
- Re-read the document for the specific fields named. Most failures come from a \
value read from the wrong column, a VAT category inferred from a rate instead of \
from the wording, or an identifier transcribed with a character wrong.
- Identifiers are checksummed. A Belgian enterprise number that fails its check \
has almost certainly been misread by one digit; look again rather than adjusting \
it to pass.
- If a field the rules require is genuinely not printed on the document, leave it \
null and record an uncertainty saying so. Do not invent a value to satisfy a \
rule. An invoice that fails validation for an honest reason is worth more than \
one that passes because a field was fabricated.

Return the complete corrected extraction, not only the changed fields.\
"""


def describe(finding: ValidationFinding) -> str:
    """Render one validation finding as a problem, not as validator output."""
    layer = {"xsd": "UBL schema", "en16931": "EN 16931", "peppol": "Peppol BIS 3.0"}
    where = f"\n  location: {finding.location}" if finding.location else ""
    return (
        f"- [{layer.get(finding.layer.value, finding.layer.value)} "
        f"{finding.rule_id or 'unnamed rule'}] {finding.message}{where}"
    )


def build_repair_message(
    previous: ExtractedInvoice,
    validation: ValidationResult,
    problems: list[Problem],
) -> str:
    """Describe what the first reading got wrong, grouped by how it was caught."""
    sections: list[str] = []

    if validation.blocking:
        sections += [
            "The document built from the first reading failed validation:",
            "",
            *(describe(finding) for finding in validation.blocking),
        ]

    reconciliation = [p for p in problems if p.kind is ProblemKind.RECONCILIATION]
    if reconciliation:
        if sections:
            sections.append("")
        sections += [
            "The line items do not add up to the totals printed on the document:",
            "",
            *(f"- {problem}" for problem in reconciliation),
        ]

    other = [
        p for p in problems if p.needs_human and p.kind is not ProblemKind.RECONCILIATION
    ]
    if other:
        if sections:
            sections.append("")
        sections += [
            "The mapping step also flagged these, which may share a cause:",
            "",
            *(f"- {problem}" for problem in other),
        ]

    sections += [
        "",
        "This was the previous reading:",
        "",
        previous.model_dump_json(indent=2, exclude_none=True),
        "",
        "Re-read the attached document and return the corrected extraction.",
    ]
    return "\n".join(sections)


@dataclass(frozen=True)
class RepairResult:
    invoice: ExtractedInvoice
    spend: Spend
    latency_ms: int


def repair(
    document: Path,
    previous: ExtractedInvoice,
    validation: ValidationResult,
    problems: list[Problem],
    *,
    arm: Arm | str = Arm.VISION,
    budget: Budget,
    client=None,
    model: str = DEFAULT_MODEL,
    effort: str | None = REPAIR_EFFORT,
) -> RepairResult:
    """Ask for one corrected extraction. The document is re-sent so it can be re-read."""
    arm = Arm(arm)

    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    content = build_content(document, arm)
    content.append(
        {"type": "text", "text": build_repair_message(previous, validation, problems)}
    )

    response = structured_request(
        client,
        model=model,
        system=REPAIR_SYSTEM_PROMPT,
        content=content,
        output_format=ExtractedInvoice,
        budget=budget,
        about=f"repair {document.name}",
        effort=effort,
    )
    return RepairResult(
        invoice=response.parsed,
        spend=response.spend,
        latency_ms=response.latency_ms,
    )
