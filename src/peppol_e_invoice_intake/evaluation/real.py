"""A small evaluation on real supplier invoices.

The synthetic corpus saturates: at 99.9% it can no longer tell a good pipeline
from a better one, because every document in it is clean, digital and generated
by this project. Real invoices - scanned, photographed, laid out by someone else -
are the honest test. This module scores a handful of them on the fields that
matter most, rather than every line.

**Labels are typed by a person from the paper, never pre-filled by the model.**
Pre-filling would be faster, but the labeller then checks the model's answers
against the model's answers, and the score measures agreement with itself.

Real invoices are other people's commercial data. They live under the gitignored
`corpus/real/`, their results under the gitignored `runs/`, and only aggregate
numbers are ever published.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict
from pathlib import Path

from ..corpus.fields import ALL_FIELDS, extract_ground_truth, values_match
from ..pipeline.budget import Budget, BudgetExceeded
from ..pipeline.runner import run
from .evaluate import (
    AuthenticationFailed,
    EvalConfig,
    _append,
    _bind_config,
    _is_auth_failure,
    _read_rows,
)

REAL_ROOT = Path("corpus/real")

#: The fields a person checks on every real invoice: identity, the parties, how to
#: pay, and the four totals. Line items are left out on purpose - labelling every
#: line of a real invoice by hand is where an evaluation like this stalls.
KEY_FIELDS: tuple[str, ...] = (
    "BT-1",    # invoice number
    "BT-2",    # issue date
    "BT-9",    # due date
    "BT-27",   # seller name
    "BT-31",   # seller VAT number
    "BT-44",   # buyer name
    "BT-48",   # buyer VAT number
    "BT-84",   # IBAN
    "BT-109",  # total excluding VAT
    "BT-110",  # total VAT
    "BT-112",  # total including VAT
    "BT-115",  # amount due
)

LABEL_INSTRUCTIONS = (
    "Type each value exactly as the invoice states it, in canonical form: dates as "
    "YYYY-MM-DD, amounts with a dot as the decimal separator and no thousands "
    "grouping (1520.50), identifiers character for character. Leave a field as an "
    "empty string when the invoice does not print it. Set 'verified' to true only "
    "once every field has been checked against the paper."
)


def label_path(pdf: Path) -> Path:
    return pdf.with_suffix(".labels.json")


def init_labels(root: Path = REAL_ROOT) -> list[Path]:
    """Write a blank label file beside every PDF that does not have one yet."""
    created = []
    for pdf in sorted(root.glob("*.pdf")):
        path = label_path(pdf)
        if path.exists():
            continue
        path.write_text(
            json.dumps(
                {
                    "instructions": LABEL_INSTRUCTIONS,
                    "verified": False,
                    "fields": {
                        bt: {"name": ALL_FIELDS[bt].name, "value": ""} for bt in KEY_FIELDS
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        created.append(path)
    return created


class LabelError(ValueError):
    """A label file that cannot be used as ground truth."""


def load_labels(path: Path) -> dict[str, str | None]:
    """Read a verified label file into {BT: value or None}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("verified") is not True:
        raise LabelError(f"{path.name} is not marked verified; check it against the paper")
    fields = data.get("fields", {})
    missing = [bt for bt in KEY_FIELDS if bt not in fields]
    if missing:
        raise LabelError(f"{path.name} is missing {', '.join(missing)}")
    return {bt: (str(fields[bt]["value"]).strip() or None) for bt in KEY_FIELDS}


def score_real(labels: dict[str, str | None], xml: bytes | None) -> dict:
    """Score the key fields of one invoice. Same rules as the synthetic scorer:
    a field the invoice prints and the pipeline missed is a miss; a field the
    pipeline produced that the invoice does not print is spurious, not a miss."""
    predicted = extract_ground_truth(xml)["document"] if xml else {}
    correct = total = spurious = 0
    misses = []
    for bt in KEY_FIELDS:
        want, got = labels[bt], predicted.get(bt)
        if want is None:
            spurious += got is not None
            continue
        total += 1
        if values_match(ALL_FIELDS[bt].kind, want, got):
            correct += 1
        else:
            misses.append({"field": bt, "name": ALL_FIELDS[bt].name,
                           "expected": want, "actual": got})
    return {
        "fields_total": total,
        "fields_correct": correct,
        "fields_spurious": spurious,
        "accuracy": None if total == 0 else correct / total,
        "misses": misses,
    }


def labelled_documents(root: Path = REAL_ROOT) -> tuple[list[Path], list[str]]:
    """PDFs with verified labels, and a note for every PDF that was skipped."""
    ready, skipped = [], []
    for pdf in sorted(root.glob("*.pdf")):
        path = label_path(pdf)
        if not path.exists():
            skipped.append(f"{pdf.name}: no label file (run `real init`)")
            continue
        try:
            load_labels(path)
        except (LabelError, json.JSONDecodeError) as exc:
            skipped.append(str(exc))
            continue
        ready.append(pdf)
    return ready, skipped


def evaluate_real(
    out: Path,
    config: EvalConfig,
    *,
    budget: Budget,
    root: Path = REAL_ROOT,
    client=None,
    on_result=None,
) -> dict:
    """Run and score every verified real invoice. Resumable, like the synthetic run."""
    documents, skipped = labelled_documents(root)
    if not documents:
        raise LabelError("no verified label files in " + str(root))

    out.mkdir(parents=True, exist_ok=True)
    _bind_config(out, config, [{"pdf": pdf.name} for pdf in documents])
    results_path = out / "results.jsonl"
    done = {row["pdf"] for row in _read_rows(results_path)}
    stopped = None

    for pdf in documents:
        if pdf.name in done:
            continue
        labels = load_labels(label_path(pdf))
        try:
            result = run(
                pdf, arm=config.arm, budget=budget, client=client, model=config.model,
                allow_repair=config.allow_repair, extract_effort=config.extract_effort,
                repair_effort=config.repair_effort, workdir=out / "documents",
            )
        except BudgetExceeded as exc:
            stopped = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 - classified below
            if _is_auth_failure(exc):
                raise AuthenticationFailed(str(exc)) from exc
            _append(out / "errors.jsonl", {"pdf": pdf.name, "error": repr(exc)})
            continue

        row = {
            "pdf": pdf.name,
            **{k: v for k, v in result.summary().items() if k != "document"},
            "needs_human_detail": [str(problem) for problem in result.needs_human],
            "score": score_real(labels, result.xml),
        }
        _append(results_path, row)
        if on_result is not None:
            on_result(row)

    rows = _read_rows(results_path)
    summary = summarize_real(rows)
    summary.update(stopped=stopped, skipped=skipped, budget=budget.summary(),
                   config=asdict(config))
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def summarize_real(rows: list[dict]) -> dict:
    correct = sum(row["score"]["fields_correct"] for row in rows)
    total = sum(row["score"]["fields_total"] for row in rows)
    cents = [row["usd_cents"] for row in rows]
    latency = [row["latency_ms"] for row in rows]

    def share(key: str) -> float | None:
        return None if not rows else sum(1 for row in rows if row.get(key)) / len(rows)

    return {
        "documents": len(rows),
        "field_accuracy": None if total == 0 else correct / total,
        "fields_scored": total,
        "fields_correct": correct,
        "fields_spurious": sum(row["score"]["fields_spurious"] for row in rows),
        "valid_first_attempt": share("valid_first_attempt"),
        "valid_after_repair": share("valid"),
        "reconciles_after_repair": share("reconciles"),
        "needing_a_person": sum(1 for row in rows if row["needs_human"]),
        "errors": sum(1 for row in rows if row.get("error")),
        "cost_cents_median": statistics.median(cents) if cents else None,
        "cost_cents_total": sum(cents),
        "latency_ms_median": statistics.median(latency) if latency else None,
        "misses_by_field": {
            bt: sum(1 for row in rows for m in row["score"]["misses"] if m["field"] == bt)
            for bt in KEY_FIELDS
        },
    }
