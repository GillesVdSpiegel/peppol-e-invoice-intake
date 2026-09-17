"""The pipeline: PDF in, validated UBL plus a report of what could not be mapped.

    extract -> map -> emit -> validate -> (one repair attempt) -> emit -> validate

Every stage records what it could not do confidently. The result carries both the
document and the doubts about it, because a Peppol file that validates is not the
same thing as a Peppol file that is correct, and the difference is what a person
needs to see.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from ..corpus.model import Invoice, Language
from ..corpus.ubl import to_xml
from ..validation import ValidationResult, validate
from .budget import DEFAULT_MODEL, Budget
from .extraction import Arm, ExtractionResult, extract
from .mapping import Problem, ProblemKind, map_to_invoice
from .repair import repair
from .request import ModelRequestError
from .schema import ExtractedInvoice


@dataclass
class PipelineResult:
    """Everything one document produced, including what went wrong."""

    document: Path
    arm: Arm
    model: str

    extraction: ExtractedInvoice | None = None
    invoice: Invoice | None = None
    xml: bytes | None = None
    validation: ValidationResult | None = None

    #: Validation of the first attempt, kept even when a repair supersedes it,
    #: so "valid first time" and "valid after repair" are both reportable.
    first_validation: ValidationResult | None = None
    repair_attempted: bool = False

    problems: list[Problem] = field(default_factory=list)
    error: str | None = None

    usd: Decimal = Decimal(0)
    latency_ms: int = 0

    @property
    def valid(self) -> bool:
        return self.validation is not None and self.validation.is_valid

    @property
    def valid_first_attempt(self) -> bool:
        return self.first_validation is not None and self.first_validation.is_valid

    @property
    def repaired_successfully(self) -> bool:
        return self.repair_attempted and self.valid and not self.valid_first_attempt

    @property
    def needs_human(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.needs_human]

    @property
    def reconciles(self) -> bool:
        """True when the computed totals match the ones printed on the document.

        A document can validate and still fail this: validation only ever sees
        the computed, self-consistent totals.
        """
        return not any(
            problem.kind is ProblemKind.RECONCILIATION for problem in self.problems
        )

    def summary(self) -> dict:
        return {
            "document": self.document.name,
            "arm": self.arm.value,
            "model": self.model,
            "valid": self.valid,
            "valid_first_attempt": self.valid_first_attempt,
            "repair_attempted": self.repair_attempted,
            "reconciles": self.reconciles,
            "problems": len(self.problems),
            "needs_human": len(self.needs_human),
            "rules_failed": sorted(self.validation.rule_ids()) if self.validation else [],
            "usd_cents": float(round(self.usd * 100, 4)),
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


def _emit_and_validate(
    invoice: Invoice, workdir: Path, name: str
) -> tuple[bytes, ValidationResult]:
    xml = to_xml(invoice)
    path = workdir / f"{name}.xml"
    path.write_bytes(xml)
    return xml, validate(path)


def run(
    document: Path,
    *,
    arm: Arm | str = Arm.VISION,
    budget: Budget,
    client=None,
    model: str = DEFAULT_MODEL,
    language: Language = Language.NL,
    allow_repair: bool = True,
    workdir: Path | None = None,
) -> PipelineResult:
    """Process one invoice PDF. Makes one or two paid requests."""
    arm = Arm(arm)
    result = PipelineResult(document=document, arm=arm, model=model)
    started = time.perf_counter()

    created_workdir = workdir is None
    workdir = Path(tempfile.mkdtemp()) if created_workdir else workdir
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        extraction: ExtractionResult = extract(
            document, arm=arm, budget=budget, client=client, model=model
        )
    except ModelRequestError as exc:
        result.error = str(exc)
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    result.extraction = extraction.invoice
    result.usd += extraction.spend.usd

    mapping = map_to_invoice(extraction.invoice, language=language)
    result.problems = list(mapping.problems)
    if mapping.invoice is None:
        result.error = "the document could not be mapped to an invoice"
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    result.invoice = mapping.invoice
    result.xml, validation = _emit_and_validate(mapping.invoice, workdir, document.stem)
    result.first_validation = validation
    result.validation = validation

    if not validation.is_valid and allow_repair:
        result.repair_attempted = True
        try:
            repaired = repair(
                document,
                extraction.invoice,
                validation,
                mapping.problems,
                arm=arm,
                budget=budget,
                client=client,
                model=model,
            )
        except ModelRequestError as exc:
            result.error = f"repair failed: {exc}"
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result

        result.usd += repaired.spend.usd
        remapped = map_to_invoice(repaired.invoice, language=language)
        if remapped.invoice is not None:
            xml, revalidation = _emit_and_validate(
                remapped.invoice, workdir, f"{document.stem}-repaired"
            )
            # The repair is kept only if it actually helped. A second attempt that
            # validates no better is not an improvement, and keeping it would hide
            # the original reading behind a rewrite of unknown quality.
            if revalidation.is_valid or len(revalidation.blocking) < len(validation.blocking):
                result.extraction = repaired.invoice
                result.invoice = remapped.invoice
                result.xml = xml
                result.validation = revalidation
                result.problems = list(remapped.problems)

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result
