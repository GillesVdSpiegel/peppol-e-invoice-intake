from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..artefacts import RuleSet
from ..findings import ValidationFinding


class UnavailableBackend(RuntimeError):
    """Raised when a backend's external prerequisites are not present."""


@runtime_checkable
class SchematronBackend(Protocol):
    name: str

    def available(self) -> bool:
        """True when this backend can run right now (deps present, artefacts fetched)."""
        ...

    def unavailable_reason(self) -> str | None:
        """Human-readable explanation when `available()` is False."""
        ...

    def validate(
        self, document: Path, rule_sets: tuple[RuleSet, ...]
    ) -> tuple[ValidationFinding, ...]:
        ...
