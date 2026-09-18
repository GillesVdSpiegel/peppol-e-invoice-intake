"""The one place a request is made, so extraction and repair behave alike.

Three things happen here in a deliberate order, and the order is the point:

1. **Usage is recorded first**, unconditionally. A truncated or malformed
   response was still billed, and a budget that only counts the successes
   undercounts exactly when something is going wrong.
2. **`stop_reason` is checked before the text is touched.** A response cut off
   at the output ceiling can hold a plausible-looking half of an invoice.
3. **Only then is the JSON validated against the schema**, by this code rather
   than inside the SDK. Letting the SDK parse during streaming raises a pydantic
   error mid-stream on a truncated response, before the stop reason or the final
   usage has arrived - which is how this module first found out.

Requests stream because the output ceiling is high enough that the SDK refuses
to send them any other way; a long line-item list produces a lot of JSON.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

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


def _wire(node: Any, *, inside_properties: bool = False) -> Any:
    """Collapse `X | null` unions to `X` and drop titles.

    Structured outputs compile the schema into a grammar, and each nullable field
    is a union in that grammar. With 38 of them the API refused the request as
    "too large" on the first real call. Absence travels as an empty string instead,
    which the schema models turn back into None on parsing.

    Titles only restate the property names, so they are dropped too: they cost
    input tokens on every request and tell the model nothing.
    """
    if isinstance(node, list):
        return [_wire(item) for item in node]
    if not isinstance(node, dict):
        return node
    if inside_properties:
        # Keys here are field names, not schema keywords; keep every one.
        return {name: _wire(spec) for name, spec in node.items()}

    options = node.get("anyOf")
    if isinstance(options, list):
        concrete = [option for option in options if option != {"type": "null"}]
        if len(concrete) == 1 and len(concrete) < len(options):
            node = {**{k: v for k, v in node.items() if k != "anyOf"}, **concrete[0]}

    return {
        key: _wire(value, inside_properties=key == "properties")
        for key, value in node.items()
        if key != "title"
    }


@lru_cache(maxsize=8)
def json_schema_for(output_format: type[BaseModel]) -> dict:
    """The schema actually sent: structured-outputs dialect, no nullable unions."""
    from anthropic import transform_schema

    return _wire(transform_schema(TypeAdapter(output_format).json_schema()))


def _response_text(message) -> str:
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


def structured_request(
    client,
    *,
    model: str,
    system: str,
    content: list[dict],
    output_format: type[BaseModel],
    budget: Budget,
    about: str,
    effort: str | None = None,
) -> ModelResponse:
    """Make one streamed, structured request and account for what it cost.

    `effort` is the main cost lever for this workload. Output tokens cost five
    times what input does, and effort scales how much the model thinks before it
    writes the JSON. None leaves the model's default.
    """
    budget.check(about_to=about)

    output_config: dict = {
        "format": {"type": "json_schema", "schema": json_schema_for(output_format)}
    }
    if effort is not None:
        output_config["effort"] = effort

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
        output_config=output_config,
    ) as stream:
        message = stream.get_final_message()

    latency_ms = int((time.perf_counter() - started) * 1000)
    spend = budget.record(Spend.from_usage(model, message.usage))

    if message.stop_reason == "max_tokens":
        raise ModelRequestError(
            f"{about}: the response hit the {MAX_OUTPUT_TOKENS} token output ceiling "
            "and was truncated, so it cannot be trusted as a reading."
        )
    if message.stop_reason == "refusal":
        raise ModelRequestError(f"{about}: the model declined to process this document.")

    text = _response_text(message)
    if not text.strip():
        raise ModelRequestError(f"{about}: no structured output was returned.")
    try:
        parsed = output_format.model_validate_json(text)
    except ValidationError as exc:
        raise ModelRequestError(
            f"{about}: the response did not match the extraction schema "
            f"({exc.error_count()} error(s))."
        ) from exc

    return ModelResponse(parsed=parsed, spend=spend, latency_ms=latency_ms)
