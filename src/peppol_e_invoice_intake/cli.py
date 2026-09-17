"""Command line entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

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
