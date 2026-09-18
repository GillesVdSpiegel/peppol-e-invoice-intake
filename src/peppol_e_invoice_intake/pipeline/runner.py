"""The pipeline: PDF in, validated UBL plus a report of what could not be mapped.

    extract -> map -> emit -> validate + reconcile -> (one repair) -> emit -> validate

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
from .budget import DEFAULT_MODEL, Budget, Spend
from .extraction import EXTRACT_EFFORT, Arm, ExtractionResult, extract
from .mapping import MappingResult, Problem, ProblemKind, map_to_invoice
from .repair import REPAIR_EFFORT, repair
from .request import ModelRequestError
from .schema import ExtractedInvoice


def _reconciliation_count(problems: list[Problem]) -> int:
    return sum(1 for problem in problems if problem.kind is ProblemKind.RECONCILIATION)


def _badness(validation: ValidationResult, problems: list[Problem]) -> int:
    """How far a reading is from acceptable. Lower is better; zero is clean.

    Blocking validation findings and reconciliation disagreements count equally:
    each is concrete evidence that something on the page was read wrong.
    """
    return len(validation.blocking) + _reconciliation_count(problems)


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

    #: The first reading's outcome, kept even when a repair supersedes it, so
    #: "right first time" and "right after repair" are both reportable.
    first_validation: ValidationResult | None = None
    first_reconciles: bool | None = None
    repair_attempted: bool = False
    repair_kept: bool = False

    problems: list[Problem] = field(default_factory=list)
    error: str | None = None

    spends: list[Spend] = field(default_factory=list)
    latency_ms: int = 0

    @property
    def usd(self) -> Decimal:
        return sum((spend.usd for spend in self.spends), Decimal(0))

    @property
    def valid(self) -> bool:
        return self.validation is not None and self.validation.is_valid

    @property
    def valid_first_attempt(self) -> bool:
        return self.first_validation is not None and self.first_validation.is_valid

    @property
    def repaired_successfully(self) -> bool:
        return self.repair_kept and self.valid and not self.valid_first_attempt

    @property
    def needs_human(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.needs_human]

    @property
    def reconciles(self) -> bool:
        """True when the computed totals match the ones printed on the document.

        A document can validate and still fail this: validation only ever sees
        the computed, self-consistent totals.
        """
        return _reconciliation_count(self.problems) == 0

    def summary(self) -> dict:
        return {
            "document": self.document.name,
            "arm": self.arm.value,
            "model": self.model,
            "valid": self.valid,
            "valid_first_attempt": self.valid_first_attempt,
            "reconciles": self.reconciles,
            "reconciles_first_attempt": self.first_reconciles,
            "repair_attempted": self.repair_attempted,
            "repair_kept": self.repair_kept,
            "problems": len(self.problems),
            "needs_human": len(self.needs_human),
            "rules_failed": sorted(self.validation.rule_ids()) if self.validation else [],
            "requests": len(self.spends),
            "input_tokens": sum(s.input_tokens for s in self.spends),
            "output_tokens": sum(s.output_tokens for s in self.spends),
            "cache_read_tokens": sum(s.cache_read_tokens for s in self.spends),
            "cache_write_tokens": sum(s.cache_write_tokens for s in self.spends),
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


def _needs_repair(validation: ValidationResult, mapping: MappingResult) -> bool:
    """Repair when there is concrete evidence of a misreading.

    Validation alone is a weak trigger here: totals are recomputed, so a misread
    line still yields a self-consistent, valid document. The totals printed on
    the page disagreeing with the lines is the stronger signal.
    """
    return not validation.is_valid or _reconciliation_count(mapping.problems) > 0


def run(
    document: Path,
    *,
    arm: Arm | str = Arm.VISION,
    budget: Budget,
    client=None,
    model: str = DEFAULT_MODEL,
    language: Language = Language.NL,
    allow_repair: bool = True,
    extract_effort: str | None = EXTRACT_EFFORT,
    repair_effort: str | None = REPAIR_EFFORT,
    workdir: Path | None = None,
) -> PipelineResult:
    """Process one invoice PDF. Makes one paid request, or two if it needs repair."""
    arm = Arm(arm)
    result = PipelineResult(document=document, arm=arm, model=model)
    started = time.perf_counter()

    workdir = workdir if workdir is not None else Path(tempfile.mkdtemp())
    workdir.mkdir(parents=True, exist_ok=True)

    def finish() -> PipelineResult:
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    try:
        extraction: ExtractionResult = extract(
            document, arm=arm, budget=budget, client=client, model=model,
            effort=extract_effort,
        )
    except ModelRequestError as exc:
        result.error = str(exc)
        return finish()

    result.extraction = extraction.invoice
    result.spends.append(extraction.spend)

    mapping = map_to_invoice(extraction.invoice, language=language)
    result.problems = list(mapping.problems)
    if mapping.invoice is None:
        result.error = "the document could not be mapped to an invoice"
        return finish()

    result.invoice = mapping.invoice
    result.xml, validation = _emit_and_validate(mapping.invoice, workdir, document.stem)
    result.first_validation = validation
    result.validation = validation
    result.first_reconciles = _reconciliation_count(mapping.problems) == 0

    if not (allow_repair and _needs_repair(validation, mapping)):
        return finish()

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
            effort=repair_effort,
        )
    except ModelRequestError as exc:
        # The first reading still stands; a failed repair does not erase it.
        result.error = f"repair failed: {exc}"
        return finish()

    result.spends.append(repaired.spend)
    remapped = map_to_invoice(repaired.invoice, language=language)
    if remapped.invoice is None:
        return finish()

    xml, revalidation = _emit_and_validate(
        remapped.invoice, workdir, f"{document.stem}-repaired"
    )
    # Kept only if it is strictly better. A second reading that is no better is
    # not an improvement, and keeping it would hide the original behind a rewrite
    # of unknown quality.
    if _badness(revalidation, remapped.problems) < _badness(validation, mapping.problems):
        result.repair_kept = True
        result.extraction = repaired.invoice
        result.invoice = remapped.invoice
        result.xml = xml
        result.validation = revalidation
        result.problems = list(remapped.problems)

    return finish()
