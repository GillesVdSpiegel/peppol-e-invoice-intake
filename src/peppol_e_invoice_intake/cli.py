"""Command line entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .corpus.build import DEFAULT_ROOT, CorpusBuildError, build, load_manifest
from .corpus.catalogue import CATALOGUE
from .corpus.render import LAYOUTS
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


if __name__ == "__main__":
    app()


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
