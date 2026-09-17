"""Build the corpus: UBL, ground truth and PDFs, with a validation gate.

Nothing is written unless the generated UBL passes the phase 1 validator. A
corpus whose documents are not actually Peppol-compliant would make every
downstream number meaningless, so an invalid document fails the build rather than
landing on disk with a warning.

Output layout:

    corpus/synthetic/
        manifest.json
        ubl/<key>.xml
        ground-truth/<key>.json
        pdf/<key>__<layout>.pdf

Ground truth is per base invoice rather than per PDF: the layout changes how a
document looks, never what it says, so all renders of one invoice share a single
set of labels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..validation import ValidationResult, validate
from .catalogue import CATALOGUE
from .fields import extract_ground_truth
from .model import Invoice
from .render import LAYOUTS, Layout, PdfRenderer
from .ubl import to_xml

DEFAULT_ROOT = Path("corpus/synthetic")

#: How many layouts each base invoice is rendered in by default.
#: Twenty invoices at three layouts is sixty documents - enough for the phase 4
#: numbers to mean something, small enough that a full evaluation run costs cents
#: rather than tens of euros. `--full` renders every layout instead.
DEFAULT_LAYOUTS_PER_INVOICE = 3


class CorpusBuildError(RuntimeError):
    """Raised when a generated document fails validation."""


@dataclass(frozen=True)
class Document:
    """One rendered PDF and the files it is scored against."""

    key: str
    layout: str
    language: str
    pdf: Path
    ubl: Path
    ground_truth: Path

    def as_manifest_entry(self, root: Path) -> dict:
        return {
            "key": self.key,
            "layout": self.layout,
            "language": self.language,
            "pdf": self.pdf.relative_to(root).as_posix(),
            "ubl": self.ubl.relative_to(root).as_posix(),
            "ground_truth": self.ground_truth.relative_to(root).as_posix(),
        }


def layouts_for(index: int, count: int = DEFAULT_LAYOUTS_PER_INVOICE) -> list[Layout]:
    """Pick layouts for one invoice, deterministically and evenly.

    The stride of five is coprime with six, so the layouts chosen for an invoice
    are always distinct and each layout ends up used the same number of times
    across the catalogue. Deterministic because a corpus that changes between
    runs cannot be compared against itself.
    """
    if count > len(LAYOUTS):
        raise ValueError(f"cannot pick {count} distinct layouts from {len(LAYOUTS)}")
    return [LAYOUTS[(index + offset * 5) % len(LAYOUTS)] for offset in range(count)]


def validate_invoice(key: str, invoice: Invoice, destination: Path) -> ValidationResult:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(to_xml(invoice))
    return validate(destination)


def build(
    root: Path = DEFAULT_ROOT,
    *,
    catalogue: dict[str, Invoice] | None = None,
    layouts_per_invoice: int = DEFAULT_LAYOUTS_PER_INVOICE,
    full: bool = False,
    render_pdfs: bool = True,
    on_progress=None,
) -> list[Document]:
    """Generate the corpus. Returns one entry per rendered PDF."""
    invoices = catalogue if catalogue is not None else CATALOGUE
    count = len(LAYOUTS) if full else layouts_per_invoice

    root.mkdir(parents=True, exist_ok=True)
    documents: list[Document] = []
    failures: list[str] = []

    renderer = PdfRenderer() if render_pdfs else None
    if renderer is not None:
        renderer.__enter__()
    try:
        for index, (key, invoice) in enumerate(invoices.items()):
            ubl_path = root / "ubl" / f"{key}.xml"
            result = validate_invoice(key, invoice, ubl_path)
            if not result.is_valid:
                ubl_path.unlink(missing_ok=True)
                rules = ", ".join(sorted(result.rule_ids())) or "(no rule id)"
                failures.append(f"{key}: {rules}")
                continue

            truth_path = root / "ground-truth" / f"{key}.json"
            truth_path.parent.mkdir(parents=True, exist_ok=True)
            truth_path.write_text(
                json.dumps(extract_ground_truth(ubl_path.read_bytes()), indent=2,
                           ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            for layout in layouts_for(index, count):
                pdf_path = root / "pdf" / f"{key}__{layout.name}.pdf"
                if renderer is not None:
                    renderer.render(invoice, pdf_path, layout)
                document = Document(
                    key=key,
                    layout=layout.name,
                    language=invoice.language.value,
                    pdf=pdf_path,
                    ubl=ubl_path,
                    ground_truth=truth_path,
                )
                documents.append(document)
                if on_progress is not None:
                    on_progress(document)
    finally:
        if renderer is not None:
            renderer.__exit__(None, None, None)

    if failures:
        raise CorpusBuildError(
            "Generated UBL failed validation, so the corpus was not written:\n  "
            + "\n  ".join(failures)
        )

    manifest = {
        "base_invoices": len(invoices),
        "layouts_per_invoice": count,
        "documents": len(documents),
        "entries": [document.as_manifest_entry(root) for document in documents],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return documents


def load_manifest(root: Path = DEFAULT_ROOT) -> dict:
    path = root / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No corpus manifest at {path}. Run: peppol-e-invoice-intake corpus build"
        )
    return json.loads(path.read_text(encoding="utf-8"))
