"""The one shape every validation layer and backend reduces to.

This module is deliberately the smallest thing in the package. `ValidationFinding`
is the contract the repair loop consumes in phase 3: the model is handed described
problems, never raw validator output, so this type is what "described" means.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Layer(StrEnum):
    """Which rule set produced a finding. Peppol rejects things EN 16931 allows,
    so the layer is reported separately rather than merged into one pass/fail."""

    XSD = "xsd"
    EN16931 = "en16931"
    PEPPOL = "peppol"


class Severity(StrEnum):
    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"


BLOCKING = frozenset({Severity.FATAL, Severity.ERROR})


class ValidationFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    layer: Layer
    severity: Severity
    message: str
    rule_id: str | None = None
    location: str | None = None
    test: str | None = None

    @property
    def is_blocking(self) -> bool:
        return self.severity in BLOCKING

    @property
    def identity(self) -> tuple[str, str | None, str]:
        """Backend-independent identity, used to assert the two backends agree.

        Deliberately excludes message text and location: those are formatted
        differently by each backend and carry no extra information about which
        rule fired.
        """
        return (self.layer.value, self.rule_id, self.severity.value)

    def __str__(self) -> str:
        rule = self.rule_id or "(unnamed)"
        where = f" at {self.location}" if self.location else ""
        return f"[{self.layer.value}/{self.severity.value}] {rule}: {self.message}{where}"


class ValidationResult(BaseModel):
    document: str
    backend: str
    findings: tuple[ValidationFinding, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not any(f.is_blocking for f in self.findings)

    @property
    def blocking(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.is_blocking)

    @property
    def warnings(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if not f.is_blocking)

    def rule_ids(self, *, blocking_only: bool = True) -> set[str]:
        source = self.blocking if blocking_only else self.findings
        return {f.rule_id for f in source if f.rule_id}

    def identities(self) -> set[tuple[str, str | None, str]]:
        return {f.identity for f in self.findings}

    def for_layer(self, layer: Layer) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.layer is layer)
