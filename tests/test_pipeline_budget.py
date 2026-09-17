"""The spend ceiling. Pure arithmetic - no network, no spend."""

from __future__ import annotations

from decimal import Decimal

import pytest

from peppol_e_invoice_intake.pipeline.budget import (
    PRICING,
    Budget,
    BudgetExceeded,
    Spend,
)


def test_cost_is_computed_from_the_published_rates():
    spend = Spend("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert spend.usd == Decimal("30.00")  # $5 in + $25 out


def test_cache_reads_are_cheaper_than_fresh_input():
    fresh = Spend("claude-opus-5", input_tokens=100_000, output_tokens=0)
    cached = Spend("claude-opus-5", input_tokens=0, output_tokens=0, cache_read_tokens=100_000)
    assert cached.usd < fresh.usd
    assert cached.usd == fresh.usd / 10


def test_cache_writes_cost_more_than_fresh_input():
    fresh = Spend("claude-opus-5", input_tokens=100_000, output_tokens=0)
    written = Spend("claude-opus-5", input_tokens=0, output_tokens=0, cache_write_tokens=100_000)
    assert written.usd == fresh.usd * Decimal("1.25")


def test_an_unknown_model_raises_rather_than_costing_nothing():
    """A typo in a model name must not silently disable the ceiling."""
    spend = Spend("claude-not-a-model", input_tokens=1, output_tokens=1)
    with pytest.raises(KeyError, match="No pricing"):
        _ = spend.usd


def test_every_priced_model_has_output_dearer_than_input():
    for name, pricing in PRICING.items():
        assert pricing.output > pricing.input, name


def test_the_ceiling_is_checked_before_a_request_not_after():
    budget = Budget(limit_usd=Decimal("0.10"))
    budget.record(Spend("claude-opus-5", input_tokens=0, output_tokens=8_000))  # $0.20

    assert budget.spent_usd > budget.limit_usd
    with pytest.raises(BudgetExceeded, match="ceiling reached"):
        budget.check()


def test_the_ceiling_message_names_the_action_it_prevented():
    budget = Budget(limit_usd=Decimal("0"))
    with pytest.raises(BudgetExceeded, match="extract invoice.pdf"):
        budget.check(about_to="extract invoice.pdf")


def test_spending_below_the_ceiling_is_allowed():
    budget = Budget(limit_usd=Decimal("1.00"))
    budget.record(Spend("claude-opus-5", input_tokens=1_000, output_tokens=1_000))
    budget.check()
    assert budget.remaining_usd < Decimal("1.00")


def test_an_unlimited_budget_never_blocks():
    budget = Budget(limit_usd=None)
    budget.record(Spend("claude-opus-5", input_tokens=10_000_000, output_tokens=10_000_000))
    budget.check()
    assert budget.remaining_usd is None


def test_summary_reports_cents_because_that_is_the_published_unit():
    budget = Budget(limit_usd=Decimal("15.00"))
    budget.record(Spend("claude-opus-5", input_tokens=2_000, output_tokens=1_500))

    summary = budget.summary()
    assert summary["requests"] == 1
    assert summary["input_tokens"] == 2_000
    assert summary["usd_cents"] == pytest.approx(summary["usd"] * 100)
    assert summary["limit_usd"] == 15.0


def test_usage_objects_without_cache_fields_are_tolerated():
    class BareUsage:
        input_tokens = 10
        output_tokens = 20

    spend = Spend.from_usage("claude-opus-5", BareUsage())
    assert spend.cache_read_tokens == 0
    assert spend.total_tokens == 30
