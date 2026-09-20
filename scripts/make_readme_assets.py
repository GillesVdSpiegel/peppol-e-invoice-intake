"""Build the images at the top of the README.

    docs/images/invoice.png     the before: a corpus invoice as rendered
    docs/images/output.png      the after: the head of the UBL it converted to
    docs/images/terminal.svg    the run itself: a real `convert`, then `check`
    docs/images/terminal.png    that SVG rasterised, because GitHub strips the
                                <style> block out of an SVG in a README and every
                                colour lives in it - the image renders, but blank.
                                The READMEs reference the PNGs.

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

#: The output side of the same run, committed so a reader can open it.
OUTPUT = ROOT / "docs" / "samples" / "letterhead-fr-output.xml"
OUTPUT_LINES = 42

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


#: A file, drawn as a file. Rich's SVG export wraps everything in a terminal
#: window, which made the README's "after" image read as console output rather
#: than as the invoice itself - the one thing that picture has to convey.
OUTPUT_PAGE = """<!doctype html>
<html><body style="margin:0;padding:20px;background:#0d1117;
    font-family:ui-monospace,'Cascadia Code','Fira Code',Consolas,monospace">
  <div id="file" style="width:880px;box-sizing:border-box;background:#1f2229;
      border:1px solid #30363d;border-radius:10px;overflow:hidden">
    <div style="display:flex;justify-content:space-between;gap:32px;
        padding:11px 16px;background:#161a20;border-bottom:1px solid #30363d;
        color:#9aa4b0;font-size:14px">
      <span style="color:#e6edf3"><b>{name}</b></span>
      <span>first {shown} of {total} lines</span>
    </div>
    <pre style="margin:0;padding:16px 18px;font-size:14px;line-height:1.5;
        color:#e6edf3;white-space:pre-wrap;word-break:break-all">{code}</pre>
  </div>
</body></html>"""


def output_png() -> Path:
    """The head of the UBL the demo run produced, as the README's "after" image.

    Read from the committed copy, so the picture and the file a reader can open
    cannot drift apart. It is the top of the document, not a curated selection:
    the header says how much of it this is. Drawn as a file rather than through
    rich's SVG export, which frames everything in a terminal window - the "after"
    of a before-and-after must not look like console output.
    """
    from playwright.sync_api import sync_playwright
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import XmlLexer

    lines = OUTPUT.read_text(encoding="utf-8").splitlines()
    head = "\n".join(lines[:OUTPUT_LINES])
    code = highlight(head, XmlLexer(), HtmlFormatter(noclasses=True, nowrap=True))

    html = OUTPUT_PAGE.format(
        name=OUTPUT.name, shown=OUTPUT_LINES, total=len(lines), code=code.strip("\n")
    )

    target = IMAGES / "output.png"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 920, "height": 600}, device_scale_factor=2)
        page.set_content(html)
        page.locator("#file").screenshot(path=str(target))
        browser.close()
    return target


def terminal_png(svg: Path, name: str = "terminal.png") -> Path:
    """Rasterise a rich SVG at 2x, for a README that will not style it."""
    from playwright.sync_api import sync_playwright

    width, height = (float(value) for value in _view_box(svg)[2:])
    target = IMAGES / name
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": round(width), "height": round(height)},
            device_scale_factor=2,
        )
        page.set_content(
            f'<body style="margin:0">{svg.read_text(encoding="utf-8")}</body>'
        )
        # The stylesheet pulls Fira Code from a CDN; without this the shot can
        # land on the fallback font mid-swap.
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(target), clip={"x": 0, "y": 0, "width": width, "height": height})
        browser.close()
    return target


def _view_box(svg: Path) -> list[str]:
    import re

    match = re.search(r'viewBox="([^"]+)"', svg.read_text(encoding="utf-8"))
    if match is None:  # pragma: no cover - rich always writes one
        raise SystemExit(f"no viewBox in {svg}")
    return match.group(1).split()


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    terminal = terminal_svg()
    written = (invoice_png(), terminal, terminal_png(terminal), output_png())
    for path in written:
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
