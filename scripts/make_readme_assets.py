"""Build the two images at the top of the README.

    docs/images/invoice.png     the input: a corpus invoice as rendered
    docs/images/terminal.svg    the output: a real `convert` run, then `check`

The terminal image is made from output captured from an actual run, not
retyped. Capture it first, from the project root (the convert step is a paid API
call, a few cents):

    FORCE_COLOR=1 COLUMNS=88 peppol-e-invoice-intake convert runs/demo/invoice.pdf \\
        --out runs/demo/out --budget 0.25 > runs/demo/convert.ansi
    FORCE_COLOR=1 COLUMNS=88 peppol-e-invoice-intake check runs/demo/out/invoice.xml \\
        > runs/demo/check.ansi

Only the prompt lines are added; everything else is the captured output.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console  # noqa: E402
from rich.text import Text  # noqa: E402

from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE  # noqa: E402
from peppol_e_invoice_intake.corpus.render import render_html  # noqa: E402

DEMO = ROOT / "runs" / "demo"
IMAGES = ROOT / "docs" / "images"

DEMO_INVOICE = "reverse-charge-construction"
DEMO_LAYOUT = "letterhead"

#: The commands exactly as they were run to produce the captured output.
STEPS = (
    ("peppol-e-invoice-intake convert runs/demo/invoice.pdf --out runs/demo/out --budget 0.25",
     DEMO / "convert.ansi"),
    ("peppol-e-invoice-intake check runs/demo/out/invoice.xml", DEMO / "check.ansi"),
)


def invoice_png() -> Path:
    from playwright.sync_api import sync_playwright

    target = IMAGES / "invoice.png"
    html = render_html(CATALOGUE[DEMO_INVOICE], DEMO_LAYOUT)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 794, "height": 1123})
        # Screen rendering ignores @page margins, so add them back for the image.
        page.set_content(html.replace("<body>", '<body style="padding:15mm 20mm">', 1))
        # The invoice ends well above the fold; crop the empty lower third.
        page.screenshot(path=str(target), clip={"x": 0, "y": 0, "width": 794, "height": 860})
        browser.close()
    return target


def terminal_svg() -> Path:
    missing = [str(path) for _, path in STEPS if not path.exists()]
    if missing:
        raise SystemExit(f"Capture the demo run first (see this script's docstring): {missing}")

    console = Console(record=True, width=96, force_terminal=True, color_system="truecolor")
    for index, (command, capture) in enumerate(STEPS):
        if index:
            console.print()
        console.print(Text("$ ", style="bold green") + Text(command, style="bold"))
        console.print(Text.from_ansi(capture.read_text(encoding="utf-8").rstrip()))

    target = IMAGES / "terminal.svg"
    console.save_svg(str(target), title="peppol-e-invoice-intake")
    return target


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    for path in (invoice_png(), terminal_svg()):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
