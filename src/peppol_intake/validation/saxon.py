"""Shared SaxonC processor.

SaxonC allocates a JVM-like runtime per processor, so one is created lazily and
reused for the life of the process. Compiling a stylesheet is expensive; running
one is not.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saxonche import PySaxonProcessor, PyXslt30Processor


@lru_cache(maxsize=1)
def processor() -> PySaxonProcessor:
    from saxonche import PySaxonProcessor

    return PySaxonProcessor(license=False)


@lru_cache(maxsize=1)
def xslt_processor() -> PyXslt30Processor:
    return processor().new_xslt30_processor()
