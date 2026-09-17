"""The one place a request is made, so both extraction and repair behave alike.

Requests stream. The output ceiling is high enough that the SDK refuses to send
it any other way - a 42-line invoice produces a lot of JSON, and a non-streaming
request that large risks a ten-minute HTTP timeout. Streaming also means a slow
document does not look like a hung process.

`stop_reason` is checked before the parsed output is touched. A truncated or
declined response still carries a usable-looking object, and treating it as a
reading would put a half-transcribed invoice into the pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .budget import Budget, Spend

#: A 42-line invoice is the largest in the corpus and produces a few thousand
#: tokens of JSON. This leaves generous headroom; a document that still hits it
#: is reported as truncated rather than silently half-read.
MAX_OUTPUT_TOKENS = 32_000


class ModelRequestError(RuntimeError):
    """Raised when a response cannot be used as a reading of the document."""


@dataclass(frozen=True)
class ModelResponse:
    parsed: Any
    spend: Spend
    latency_ms: int


def structured_request(
    client,
    *,
    model: str,
    system: str,
    content: list[dict],
    output_format: type,
    budget: Budget,
    about: str,
) -> ModelResponse:
    """Make one streamed, structured request and account for what it cost."""
    budget.check(about_to=about)

    started = time.perf_counter()
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        # Identical for every document in a run, so it is worth caching.
        system=[
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": content}],
        output_format=output_format,
    ) as stream:
        response = stream.get_final_message()

    latency_ms = int((time.perf_counter() - started) * 1000)
    spend = budget.record(Spend.from_usage(model, response.usage))

    if response.stop_reason == "max_tokens":
        raise ModelRequestError(
            f"{about}: the response hit the {MAX_OUTPUT_TOKENS} token output ceiling "
            "and was truncated, so it cannot be trusted as a reading."
        )
    if response.stop_reason == "refusal":
        raise ModelRequestError(f"{about}: the model declined to process this document.")
    if response.parsed_output is None:
        raise ModelRequestError(f"{about}: no structured output was returned.")

    return ModelResponse(
        parsed=response.parsed_output, spend=spend, latency_ms=latency_ms
    )
