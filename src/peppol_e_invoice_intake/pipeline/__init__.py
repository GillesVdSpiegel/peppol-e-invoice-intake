"""Phase 3: PDF in, validated Peppol BIS Billing 3.0 UBL out.

The model reads the document; deterministic code computes the totals and builds
the XML. See docs/decisions/0002-extract-then-compute.md.
"""

from .budget import Budget, BudgetExceeded, Spend
from .extraction import Arm, ExtractionError, ExtractionResult, extract
from .mapping import MappingResult, Problem, ProblemKind, map_to_invoice
from .runner import PipelineResult, run
from .schema import ExtractedInvoice

__all__ = [
    "Arm",
    "Budget",
    "BudgetExceeded",
    "ExtractedInvoice",
    "ExtractionError",
    "ExtractionResult",
    "MappingResult",
    "PipelineResult",
    "Problem",
    "ProblemKind",
    "Spend",
    "extract",
    "map_to_invoice",
    "run",
]
