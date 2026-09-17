"""Schematron backends.

Two independent implementations run the same rule sets and reduce to the same
`ValidationFinding` type:

* ``saxon`` - SaxonC-HE in-process; the default, and what the pipeline uses.
* ``official`` - OpenPEPPOL's own pre-compiled stylesheets, used as an oracle.

Keeping both exists for tests/test_backend_agreement.py, which asserts they report
the same rules for every fixture. Without it, "this harness is correct" is an
assumption; with it, it is a measurement. See docs/decisions/0001-dual-backend.md.
"""

from __future__ import annotations

from .base import SchematronBackend, UnavailableBackend
from .official import OfficialXsltBackend
from .saxon import SaxonBackend

BACKENDS: dict[str, type] = {"saxon": SaxonBackend, "official": OfficialXsltBackend}
DEFAULT_BACKEND = "saxon"


def get_backend(name: str = DEFAULT_BACKEND) -> SchematronBackend:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown backend {name!r}; available: {', '.join(sorted(BACKENDS))}"
        ) from None


__all__ = [
    "BACKENDS",
    "DEFAULT_BACKEND",
    "OfficialXsltBackend",
    "SaxonBackend",
    "SchematronBackend",
    "UnavailableBackend",
    "get_backend",
]
