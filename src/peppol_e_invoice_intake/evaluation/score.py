"""Score an emitted invoice against ground truth, one business term at a time.

The comparison reads both sides through the same `fields` table the corpus was
built with, so the ground truth and the prediction cannot drift into different
notions of what a field is.

What counts as correct is a design decision, so it is stated here:

- **The denominator is the fields the ground truth contains.** A field the
  document genuinely carries and the pipeline did not produce is a miss.
- **A field the pipeline produced that the ground truth lacks is "spurious"**,
  reported separately rather than folded into accuracy. It is a different
  failure - inventing rather than missing - and mixing the two would hide it.
- **Amounts compare as numbers, codes case-insensitively, text with whitespace
  normalised.** A correctly read value printed differently is not an error.
- **Lines align by position.** A dropped line shifts every later line and costs
  every field after it; that is the real cost of dropping a line, so it is not
  softened by fuzzy matching.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..corpus.fields import (
    DOCUMENT_FIELDS,
    LINE_FIELDS,
    VAT_BREAKDOWN_FIELDS,
    Field,
    extract_ground_truth,
    values_match,
)

EMPTY = {"document": {}, "lines": [], "vat_breakdown": []}


@dataclass
class FieldTally:
    correct: int = 0
    total: int = 0
    spurious: int = 0

    def add(self, other: FieldTally) -> None:
        self.correct += other.correct
        self.total += other.total
        self.spurious += other.spurious

    @property
    def accuracy(self) -> float | None:
        return None if self.total == 0 else self.correct / self.total


@dataclass
class DocumentScore:
    by_field: dict[str, FieldTally] = field(default_factory=lambda: defaultdict(FieldTally))
    misses: list[dict] = field(default_factory=list)

    @property
    def overall(self) -> FieldTally:
        tally = FieldTally()
        for part in self.by_field.values():
            tally.add(part)
        return tally

    def as_dict(self) -> dict:
        overall = self.overall
        return {
            "fields_total": overall.total,
            "fields_correct": overall.correct,
            "fields_spurious": overall.spurious,
            "accuracy": overall.accuracy,
            "by_field": {
                bt: {"correct": t.correct, "total": t.total, "spurious": t.spurious}
                for bt, t in sorted(self.by_field.items())
            },
            "misses": self.misses,
        }


def _compare(
    fields: tuple[Field, ...],
    expected: dict,
    actual: dict,
    score: DocumentScore,
    where: str,
) -> None:
    for spec in fields:
        want = expected.get(spec.bt)
        got = actual.get(spec.bt)
        tally = score.by_field[spec.bt]
        if want is None:
            if got is not None:
                tally.spurious += 1
            continue
        tally.total += 1
        if values_match(spec.kind, want, got):
            tally.correct += 1
        else:
            score.misses.append(
                {"field": spec.bt, "name": spec.name, "where": where,
                 "expected": want, "actual": got}
            )


def _vat_key(row: dict) -> tuple[str, str]:
    from decimal import Decimal, InvalidOperation

    rate = row.get("BT-119", "")
    try:
        rate = str(Decimal(rate).normalize())
    except (InvalidOperation, ValueError):
        pass
    return (str(row.get("BT-118", "")).upper(), rate)


def score_document(truth: dict, predicted: dict | None) -> DocumentScore:
    """Compare two ground-truth-shaped dicts (see `extract_ground_truth`)."""
    predicted = predicted or EMPTY
    score = DocumentScore()

    _compare(DOCUMENT_FIELDS, truth["document"], predicted["document"], score, "document")

    expected_lines, actual_lines = truth["lines"], predicted["lines"]
    for index in range(max(len(expected_lines), len(actual_lines))):
        want = expected_lines[index] if index < len(expected_lines) else {}
        got = actual_lines[index] if index < len(actual_lines) else {}
        _compare(LINE_FIELDS, want, got, score, f"line {index + 1}")

    actual_rows = {_vat_key(row): row for row in predicted["vat_breakdown"]}
    matched: set[tuple[str, str]] = set()
    for row in truth["vat_breakdown"]:
        key = _vat_key(row)
        matched.add(key)
        _compare(
            VAT_BREAKDOWN_FIELDS, row, actual_rows.get(key, {}), score,
            f"VAT {key[0]} {key[1]}%",
        )
    for key, row in actual_rows.items():
        if key not in matched:
            _compare(VAT_BREAKDOWN_FIELDS, {}, row, score, f"VAT {key[0]} {key[1]}%")

    return score


def score_xml(truth: dict, xml: bytes | None) -> DocumentScore:
    """Score the UBL the pipeline emitted. No document scores as all misses."""
    return score_document(truth, extract_ground_truth(xml) if xml else None)
