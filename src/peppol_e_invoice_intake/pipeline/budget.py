"""Token accounting and a hard spend ceiling.

Cost per invoice is one of the metrics this project publishes, so spend has to be
measured rather than estimated. The same accounting doubles as the guard: the
budget is checked *before* each request, so a loop that misbehaves stops instead
of quietly running up a bill.

Prices are USD per million tokens, which is how the API bills. The project's cap
was agreed in euros; at the rates involved the difference is well inside the
margin the cap was chosen with, and the reported figure stays in the currency it
was actually charged in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens."""

    input: Decimal
    output: Decimal

    @property
    def cache_write(self) -> Decimal:
        """Writing to the cache costs about 1.25x the input rate."""
        return self.input * Decimal("1.25")

    @property
    def cache_read(self) -> Decimal:
        """Reading from the cache costs about 0.1x the input rate."""
        return self.input * Decimal("0.1")


PRICING: dict[str, ModelPricing] = {
    "claude-opus-5": ModelPricing(Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": ModelPricing(Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5": ModelPricing(Decimal("1.00"), Decimal("5.00")),
}

DEFAULT_MODEL = "claude-opus-5"


class BudgetExceeded(RuntimeError):
    """Raised before a request that would run past the spend ceiling."""


@dataclass(frozen=True)
class Spend:
    """What one request cost."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def usd(self) -> Decimal:
        pricing = PRICING.get(self.model)
        if pricing is None:
            # An unknown model must not be silently free: that would let a typo
            # disable the spend ceiling entirely.
            raise KeyError(
                f"No pricing for model {self.model!r}; add it to PRICING before use"
            )
        return (
            self.input_tokens * pricing.input
            + self.output_tokens * pricing.output
            + self.cache_read_tokens * pricing.cache_read
            + self.cache_write_tokens * pricing.cache_write
        ) / MILLION

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    @classmethod
    def from_usage(cls, model: str, usage) -> Spend:
        """Build from an SDK `response.usage`, tolerating absent cache fields."""
        return cls(
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )


@dataclass
class Budget:
    """A running total with a ceiling.

    `limit_usd` of None means unlimited, which is only ever right in tests.
    """

    limit_usd: Decimal | None = Decimal("15.00")
    spends: list[Spend] = field(default_factory=list)

    @property
    def spent_usd(self) -> Decimal:
        return sum((spend.usd for spend in self.spends), Decimal(0))

    @property
    def remaining_usd(self) -> Decimal | None:
        if self.limit_usd is None:
            return None
        return self.limit_usd - self.spent_usd

    @property
    def requests(self) -> int:
        return len(self.spends)

    def check(self, *, about_to: str = "make a request") -> None:
        """Raise if the ceiling is already reached. Called before each request."""
        if self.limit_usd is None:
            return
        if self.spent_usd >= self.limit_usd:
            raise BudgetExceeded(
                f"Spend ceiling reached before attempting to {about_to}: "
                f"${self.spent_usd:.4f} of ${self.limit_usd:.2f} across "
                f"{self.requests} request(s)."
            )

    def record(self, spend: Spend) -> Spend:
        self.spends.append(spend)
        return spend

    def summary(self) -> dict:
        return {
            "requests": self.requests,
            "input_tokens": sum(s.input_tokens for s in self.spends),
            "output_tokens": sum(s.output_tokens for s in self.spends),
            "cache_read_tokens": sum(s.cache_read_tokens for s in self.spends),
            "cache_write_tokens": sum(s.cache_write_tokens for s in self.spends),
            "usd": float(round(self.spent_usd, 6)),
            "usd_cents": float(round(self.spent_usd * 100, 4)),
            "limit_usd": None if self.limit_usd is None else float(self.limit_usd),
        }
