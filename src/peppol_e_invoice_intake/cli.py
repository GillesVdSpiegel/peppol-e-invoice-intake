"""Command line entry point."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .corpus.build import DEFAULT_ROOT, CorpusBuildError, build, load_manifest
from .corpus.catalogue import CATALOGUE
from .corpus.render import LAYOUTS
from .pipeline import Budget, BudgetExceeded, run
from .pipeline.budget import DEFAULT_MODEL
from .pipeline.extraction import EFFORT_LEVELS, EXTRACT_EFFORT
from .pipeline.repair import REPAIR_EFFORT
from .validation import ArtefactsMissing, Layer, ValidationResult, validate
from .validation.artefacts import ORACLE_RULE_SETS, RULE_SETS
from .validation.backends import BACKENDS, DEFAULT_BACKEND

app = typer.Typer(
    add_completion=False,
    help="Validate Peppol BIS Billing 3.0 UBL invoices against the official rule sets.",
)


def _utf8_console() -> Console:
    """Rule messages contain characters cp1252 cannot encode.

    EN 16931 states several arithmetic rules using a summation sign, so printing a
    finding for BR-CO-14 on a default Windows console raises UnicodeEncodeError.
    Force UTF-8 on the streams; fall back to replacement characters rather than
    letting a validation report crash on its own error text.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
            pass
    return Console()


console = _utf8_console()

_LAYER_LABEL = {
    Layer.XSD: "UBL 2.1 XSD",
    Layer.EN16931: "EN 16931",
    Layer.PEPPOL: "Peppol BIS 3.0",
}


def _render(result: ValidationResult) -> None:
    if result.is_valid and not result.findings:
        console.print(f"[bold green]VALID[/] {result.document}")
        return

    table = Table(show_lines=False, header_style="bold")
    table.add_column("Layer", no_wrap=True)
    table.add_column("Rule", no_wrap=True)
    table.add_column("Severity", no_wrap=True)
    table.add_column("Message")
    table.add_column("Location", overflow="fold", max_width=34)

    for finding in sorted(result.findings, key=lambda f: (not f.is_blocking, f.layer.value)):
        colour = "red" if finding.is_blocking else "yellow"
        table.add_row(
            _LAYER_LABEL[finding.layer],
            finding.rule_id or "-",
            f"[{colour}]{finding.severity.value}[/]",
            finding.message,
            finding.location or "-",
        )

    verdict = "[bold green]VALID[/] (with warnings)" if result.is_valid else "[bold red]INVALID[/]"
    console.print(f"{verdict} {result.document}")
    console.print(table)
    console.print(
        f"{len(result.blocking)} blocking, {len(result.warnings)} warning(s) "
        f"[dim]via {result.backend} backend[/]"
    )


@app.command()
def check(
    documents: list[Path] = typer.Argument(..., help="UBL invoice XML file(s) to validate."),
    backend: str = typer.Option(
        DEFAULT_BACKEND, "--backend", "-b", help=f"One of: {', '.join(sorted(BACKENDS))}"
    ),
    oracle_rules: bool = typer.Option(
        False,
        "--oracle-rules",
        help="Validate against the older release that has official compiled stylesheets.",
    ),
) -> None:
    """Validate invoices and exit non-zero if any document is invalid."""
    rule_sets = ORACLE_RULE_SETS if oracle_rules else RULE_SETS
    failures = 0
    for document in documents:
        if not document.exists():
            console.print(f"[bold red]MISSING[/] {document}")
            failures += 1
            continue
        try:
            result = validate(document, backend=backend, rule_sets=rule_sets)
        except ArtefactsMissing as exc:
            console.print(f"[bold red]{exc}[/]")
            raise typer.Exit(2) from None
        _render(result)
        failures += not result.is_valid
    raise typer.Exit(1 if failures else 0)


@app.command()
def rules() -> None:
    """Show which rule sets and backends are configured."""
    table = Table(header_style="bold")
    table.add_column("Set")
    table.add_column("Version")
    table.add_column("Layer")
    table.add_column("Source fetched")
    table.add_column("Official XSLT")
    for label, sets in (("production", RULE_SETS), ("oracle", ORACLE_RULE_SETS)):
        for rule_set in sets:
            table.add_row(
                label,
                rule_set.version,
                rule_set.layer.value,
                "yes" if rule_set.sch.exists() else "no",
                "yes" if rule_set.has_official_xslt else "-",
            )
    console.print(table)
    console.print(f"Backends: {', '.join(sorted(BACKENDS))} (default: {DEFAULT_BACKEND})")



corpus_app = typer.Typer(
    add_completion=False, help="Generate and inspect the synthetic test corpus."
)
app.add_typer(corpus_app, name="corpus")


@corpus_app.command("build")
def corpus_build(
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Where to write the corpus."),
    layouts: int = typer.Option(3, "--layouts", min=1, help="Layouts per base invoice."),
    full: bool = typer.Option(False, "--full", help="Render every layout for every invoice."),
    skip_pdfs: bool = typer.Option(
        False, "--skip-pdfs", help="Emit UBL and ground truth only; no browser needed."
    ),
) -> None:
    """Generate UBL, ground truth and PDFs for every catalogue invoice.

    Every document is validated before anything is written. An invalid one fails
    the build: a corpus that is not actually Peppol-compliant would make the
    published accuracy numbers meaningless.
    """
    rendered: list[str] = []
    try:
        documents = build(
            root,
            layouts_per_invoice=layouts,
            full=full,
            render_pdfs=not skip_pdfs,
            on_progress=lambda doc: rendered.append(doc.key),
        )
    except ArtefactsMissing as exc:
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(2) from None
    except CorpusBuildError as exc:
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(1) from None

    by_layout: dict[str, int] = {}
    by_language: dict[str, int] = {}
    for document in documents:
        by_layout[document.layout] = by_layout.get(document.layout, 0) + 1
        by_language[document.language] = by_language.get(document.language, 0) + 1

    console.print(
        f"[bold green]Built[/] {len(documents)} documents from "
        f"{len({d.key for d in documents})} base invoices into {root}"
    )
    table = Table(header_style="bold")
    table.add_column("Layout")
    table.add_column("Documents", justify="right")
    for name, count in sorted(by_layout.items()):
        table.add_row(name, str(count))
    console.print(table)
    console.print(
        "Languages: "
        + ", ".join(f"{lang} {count}" for lang, count in sorted(by_language.items()))
    )
    if skip_pdfs:
        console.print("[yellow]PDFs skipped (--skip-pdfs)[/]")


@corpus_app.command("list")
def corpus_list() -> None:
    """Show the catalogue of base invoices and the available layouts."""
    table = Table(header_style="bold", title="Base invoices")
    table.add_column("Key")
    table.add_column("Lang", no_wrap=True)
    table.add_column("Lines", justify="right")
    table.add_column("VAT categories")
    table.add_column("Payable", justify="right")
    for key, invoice in CATALOGUE.items():
        categories = sorted({s.category.value for s in invoice.tax_subtotals})
        table.add_row(
            key,
            invoice.language.value,
            str(len(invoice.lines)),
            " ".join(categories),
            f"{invoice.payable_amount:,.2f}",
        )
    console.print(table)

    layouts = Table(header_style="bold", title="Layouts")
    layouts.add_column("Name")
    layouts.add_column("Description")
    for layout in LAYOUTS:
        layouts.add_row(layout.name, layout.description)
    console.print(layouts)


@corpus_app.command("status")
def corpus_status(
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Corpus location."),
) -> None:
    """Report what is currently built on disk."""
    try:
        manifest = load_manifest(root)
    except FileNotFoundError as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(1) from None
    console.print(
        f"{manifest['documents']} documents from {manifest['base_invoices']} base invoices "
        f"({manifest['layouts_per_invoice']} layouts each) in {root}"
    )


def _problem_table(result) -> Table:
    table = Table(header_style="bold", title="Needs a person")
    table.add_column("Kind", no_wrap=True)
    table.add_column("Field", no_wrap=True)
    table.add_column("Detail")
    table.add_column("On the document", overflow="fold", max_width=24)
    for problem in result.needs_human:
        table.add_row(
            problem.kind.value, problem.field, problem.detail, problem.printed or "-"
        )
    return table


@app.command()
def convert(
    documents: list[Path] = typer.Argument(..., help="Invoice PDF(s) to convert."),
    out: Path = typer.Option(Path("out"), "--out", "-o", help="Where to write the UBL."),
    arm: str = typer.Option("vision", "--arm", help="vision (send the PDF) or text."),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
    budget_usd: float = typer.Option(
        15.0, "--budget", help="Hard spend ceiling in USD for this run."
    ),
    no_repair: bool = typer.Option(False, "--no-repair", help="Skip the repair attempt."),
) -> None:
    """Convert invoice PDFs to validated Peppol BIS Billing 3.0 UBL.

    This calls the Claude API and costs money. The ceiling is checked before every
    request, so the run stops rather than overspending.
    """
    budget = Budget(limit_usd=Decimal(str(budget_usd)))
    out.mkdir(parents=True, exist_ok=True)
    failures = 0

    for document in documents:
        if not document.exists():
            console.print(f"[bold red]MISSING[/] {document}")
            failures += 1
            continue
        try:
            result = run(
                document,
                arm=arm,
                budget=budget,
                model=model,
                allow_repair=not no_repair,
                workdir=out,
            )
        except BudgetExceeded as exc:
            console.print(f"[bold yellow]{exc}[/]")
            raise typer.Exit(3) from None
        except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not crash
            console.print(f"[bold red]FAILED[/] {document.name}: {exc}")
            failures += 1
            continue

        if result.error:
            console.print(f"[bold red]FAILED[/] {document.name}: {result.error}")
            failures += 1
        else:
            verdict = "[bold green]VALID[/]" if result.valid else "[bold red]INVALID[/]"
            stage = (
                "first attempt"
                if result.valid_first_attempt
                else "after repair"
                if result.repaired_successfully
                else "after repair attempt"
                if result.repair_attempted
                else "first attempt"
            )
            console.print(f"{verdict} {document.name} ({stage})")
            if not result.valid:
                failures += 1
                for finding in result.validation.blocking:
                    console.print(f"  [red]{finding.rule_id}[/] {finding.message}")
            if not result.reconciles:
                console.print(
                    "  [yellow]totals do not reconcile with the document[/] - "
                    "the emitted figures are computed, not the ones printed"
                )
            if result.needs_human:
                console.print(_problem_table(result))

        console.print(
            f"  [dim]{result.summary()['usd_cents']:.2f} cents, "
            f"{result.latency_ms} ms[/]"
        )

    summary = budget.summary()
    console.print(
        f"\nSpent ${summary['usd']:.4f} across {summary['requests']} request(s) "
        f"of a ${summary['limit_usd']:.2f} ceiling."
    )
    raise typer.Exit(1 if failures else 0)


def _pct(value) -> str:
    return "-" if value is None else f"{value:.1%}"


@app.command("eval")
def eval_command(
    sample: int = typer.Option(10, "--sample", min=1, help="Documents to evaluate."),
    out: Path = typer.Option(Path("runs/sample-10"), "--out", help="Run directory."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Corpus location."),
    arm: str = typer.Option("vision", "--arm", help="vision or text."),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
    effort: str = typer.Option(
        EXTRACT_EFFORT, "--effort", help=f"First-pass effort: {', '.join(EFFORT_LEVELS)}."
    ),
    repair_effort: str = typer.Option(REPAIR_EFFORT, "--repair-effort"),
    no_repair: bool = typer.Option(False, "--no-repair"),
    budget_usd: float = typer.Option(2.0, "--budget", help="Hard spend ceiling in USD."),
) -> None:
    """Run the pipeline over a sample of the corpus and score it against ground truth.

    Costs money. Results are written as they land, so re-running the same command
    resumes without paying again for documents already done.
    """
    from .evaluation import AuthenticationFailed, ConfigMismatch, EvalConfig, evaluate

    for level in (effort, repair_effort):
        if level not in EFFORT_LEVELS:
            console.print(f"[bold red]Unknown effort {level!r}[/]")
            raise typer.Exit(2)

    config = EvalConfig(
        arm=arm, model=model, extract_effort=effort, repair_effort=repair_effort,
        allow_repair=not no_repair, sample_size=sample,
    )
    budget = Budget(limit_usd=Decimal(str(budget_usd)))

    def progress(row: dict) -> None:
        state = "[green]valid[/]" if row["valid"] else "[red]invalid[/]"
        recon = "" if row["reconciles"] else " [yellow]does not reconcile[/]"
        accuracy = row["score"]["accuracy"]
        console.print(
            f"  {row['key']:<32} {row['layout']:<11} {state}{recon}  "
            f"fields {_pct(accuracy)}  {row['usd_cents']:.2f}c  {row['latency_ms'] / 1000:.1f}s"
        )

    try:
        summary = evaluate(root, out, config, budget=budget, on_result=progress)
    except FileNotFoundError as exc:
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(2) from None
    except ConfigMismatch as exc:
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(2) from None
    except AuthenticationFailed as exc:
        console.print(
            f"[bold red]The API rejected the credentials:[/] {exc}\n"
            "Set ANTHROPIC_API_KEY, or run `ant auth login`, and re-run."
        )
        raise typer.Exit(2) from None

    table = Table(header_style="bold", title=f"{summary['documents']} documents")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Per-field accuracy", _pct(summary["field_accuracy"]))
    table.add_row("Valid on first attempt", _pct(summary["valid_first_attempt"]))
    table.add_row("Valid after one repair", _pct(summary["valid_after_repair"]))
    table.add_row("Reconciles on first attempt", _pct(summary["reconciles_first_attempt"]))
    table.add_row("Reconciles after repair", _pct(summary["reconciles_after_repair"]))
    table.add_row("Repairs attempted / kept",
                  f"{summary['repairs_attempted']} / {summary['repairs_kept']}")
    table.add_row("Needing a person", str(summary["needing_a_person"]))
    median = summary["cost_cents_median"]
    table.add_row("Cost per invoice, median", "-" if median is None else f"{median:.2f} c")
    table.add_row("Cost, total", f"{summary['cost_cents_total']:.2f} c")
    latency = summary["latency_ms_median"]
    table.add_row("Latency, median", "-" if latency is None else f"{latency / 1000:.1f} s")
    console.print(table)
    if summary.get("stopped"):
        console.print(f"[bold yellow]Stopped early:[/] {summary['stopped']}")
    console.print(f"[dim]Full results in {out}[/]")

if __name__ == "__main__":
    app()
