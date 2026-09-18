"""Run the pipeline over a sample of the corpus and measure it.

Built so that a run never pays twice for the same document:

- Every result is appended to `results.jsonl` the moment it lands. A crash, a
  network failure or a hit spend ceiling loses nothing already paid for, and
  re-running the same command resumes where it stopped.
- A run directory is bound to one configuration. Resuming with a different
  model, effort or arm is refused, because mixing two configurations in one set
  of numbers would produce a result that describes neither.
- A transient API failure is not recorded as a result, so the document is
  retried on resume instead of being scored as a miss.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from ..corpus.build import load_manifest
from ..pipeline.budget import Budget, BudgetExceeded
from ..pipeline.runner import run
from .sample import select_sample
from .score import FieldTally, score_xml


@dataclass(frozen=True)
class EvalConfig:
    """Everything that changes what the numbers mean."""

    arm: str
    model: str
    extract_effort: str | None
    repair_effort: str | None
    allow_repair: bool
    sample_size: int


class ConfigMismatch(RuntimeError):
    """Raised when resuming a run directory with a different configuration."""


class AuthenticationFailed(RuntimeError):
    """Raised when the API rejects the credentials; nothing else can succeed."""


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _bind_config(out: Path, config: EvalConfig, sample: list[dict]) -> None:
    path = out / "config.json"
    wanted = {**asdict(config), "documents": [entry["pdf"] for entry in sample]}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != wanted:
            raise ConfigMismatch(
                f"{out} already holds a run with a different configuration. Use a new "
                "--out directory rather than mixing two configurations in one result."
            )
        return
    path.write_text(json.dumps(wanted, indent=2) + "\n", encoding="utf-8")


def evaluate(
    root: Path,
    out: Path,
    config: EvalConfig,
    *,
    budget: Budget,
    client=None,
    on_result=None,
) -> dict:
    """Run and score the sample. Returns the summary, which is also written to disk."""
    manifest = load_manifest(root)
    sample = select_sample(manifest["entries"], config.sample_size)

    out.mkdir(parents=True, exist_ok=True)
    _bind_config(out, config, sample)

    results_path = out / "results.jsonl"
    done = {row["pdf"] for row in _read_rows(results_path)}
    documents_dir = out / "documents"
    stopped: str | None = None

    for entry in sample:
        if entry["pdf"] in done:
            continue
        pdf = root / entry["pdf"]
        truth = json.loads((root / entry["ground_truth"]).read_text(encoding="utf-8"))

        try:
            result = run(
                pdf,
                arm=config.arm,
                budget=budget,
                client=client,
                model=config.model,
                allow_repair=config.allow_repair,
                extract_effort=config.extract_effort,
                repair_effort=config.repair_effort,
                workdir=documents_dir,
            )
        except BudgetExceeded as exc:
            stopped = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 - classified below
            if _is_auth_failure(exc):
                raise AuthenticationFailed(str(exc)) from exc
            _append(out / "errors.jsonl", {"pdf": entry["pdf"], "error": repr(exc)})
            continue

        if result.extraction is not None:
            documents_dir.mkdir(parents=True, exist_ok=True)
            (documents_dir / f"{pdf.stem}.extraction.json").write_text(
                result.extraction.model_dump_json(indent=2), encoding="utf-8"
            )

        row = {
            **entry,
            **result.summary(),
            "needs_human_detail": [str(problem) for problem in result.needs_human],
            "score": score_xml(truth, result.xml).as_dict(),
        }
        _append(results_path, row)
        if on_result is not None:
            on_result(row)

    rows = _read_rows(results_path)
    summary = summarize(rows, sample_size=len(sample))
    summary["stopped"] = stopped
    summary["budget"] = budget.summary()
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _is_auth_failure(exc: Exception) -> bool:
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError)


def _share(rows: list[dict], key: str) -> float | None:
    return None if not rows else sum(1 for row in rows if row.get(key)) / len(rows)


def _accuracy_by(rows: list[dict], attribute: str) -> dict[str, float | None]:
    groups: dict[str, FieldTally] = defaultdict(FieldTally)
    for row in rows:
        score = row["score"]
        groups[row[attribute]].add(
            FieldTally(score["fields_correct"], score["fields_total"], score["fields_spurious"])
        )
    return {name: tally.accuracy for name, tally in sorted(groups.items())}


def summarize(rows: list[dict], *, sample_size: int) -> dict:
    """The numbers the README reports, plus the breakdowns that explain them."""
    by_field: dict[str, FieldTally] = defaultdict(FieldTally)
    overall = FieldTally()
    for row in rows:
        for bt, counts in row["score"]["by_field"].items():
            tally = FieldTally(counts["correct"], counts["total"], counts["spurious"])
            by_field[bt].add(tally)
            overall.add(tally)

    cents = [row["usd_cents"] for row in rows]
    latency = [row["latency_ms"] for row in rows]

    return {
        "documents": len(rows),
        "sample_size": sample_size,
        "field_accuracy": overall.accuracy,
        "fields_scored": overall.total,
        "fields_spurious": overall.spurious,
        "valid_first_attempt": _share(rows, "valid_first_attempt"),
        "valid_after_repair": _share(rows, "valid"),
        "reconciles_first_attempt": _share(rows, "reconciles_first_attempt"),
        "reconciles_after_repair": _share(rows, "reconciles"),
        "repairs_attempted": sum(1 for row in rows if row["repair_attempted"]),
        "repairs_kept": sum(1 for row in rows if row["repair_kept"]),
        "needing_a_person": sum(1 for row in rows if row["needs_human"]),
        "errors": sum(1 for row in rows if row.get("error")),
        "cost_cents_mean": statistics.fmean(cents) if cents else None,
        "cost_cents_median": statistics.median(cents) if cents else None,
        "cost_cents_total": sum(cents),
        "latency_ms_median": statistics.median(latency) if latency else None,
        "latency_ms_max": max(latency) if latency else None,
        "tokens": {
            key: sum(row[key] for row in rows)
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        },
        "accuracy_by_field": {
            bt: {"accuracy": tally.accuracy, "correct": tally.correct, "total": tally.total,
                 "spurious": tally.spurious}
            for bt, tally in sorted(by_field.items(), key=lambda item: _bt_order(item[0]))
        },
        "accuracy_by_layout": _accuracy_by(rows, "layout"),
        "accuracy_by_language": _accuracy_by(rows, "language"),
    }


def _bt_order(bt: str) -> int:
    try:
        return int(bt.split("-")[1])
    except (IndexError, ValueError):  # pragma: no cover
        return 10_000
