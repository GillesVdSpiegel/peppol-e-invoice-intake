"""A stand-in for the Anthropic client, so the pipeline can be tested offline.

Every pipeline test uses this. The brief asks for tests that do not hit the
network, and that matters more here than anywhere else in the project: a suite
that called the real API would cost money on every run, produce different results
each time, and fail in CI where there is no key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from peppol_e_invoice_intake.pipeline.schema import ExtractedInvoice


@dataclass
class FakeUsage:
    input_tokens: int = 2_000
    output_tokens: int = 1_500
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: ExtractedInvoice | None
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "end_turn"


@dataclass
class FakeCall:
    """One recorded request, so tests can assert what the model was actually sent."""

    kwargs: dict[str, Any]

    @property
    def system_text(self) -> str:
        system = self.kwargs.get("system")
        if isinstance(system, str):
            return system
        return "\n".join(block.get("text", "") for block in system or [])

    @property
    def content(self) -> list[dict]:
        return self.kwargs["messages"][0]["content"]

    @property
    def text_blocks(self) -> list[str]:
        return [
            block["text"]
            for block in self.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]

    @property
    def user_text(self) -> str:
        return "\n".join(self.text_blocks)

    @property
    def has_pdf(self) -> bool:
        return any(
            isinstance(block, dict)
            and block.get("type") == "document"
            and block["source"].get("media_type") == "application/pdf"
            for block in self.content
        )


class FakeStream:
    """Stands in for the SDK's stream context manager."""

    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def get_final_message(self) -> FakeResponse:
        return self._response


class FakeMessages:
    def __init__(self, outputs: list[ExtractedInvoice | None], usage: FakeUsage | None,
                 stop_reason: str) -> None:
        self._outputs = list(outputs)
        self._usage = usage
        self._stop_reason = stop_reason
        self.calls: list[FakeCall] = []

    def _next(self, kwargs: dict) -> FakeResponse:
        self.calls.append(FakeCall(kwargs))
        # The last configured output repeats, so a test that only cares about the
        # first response does not have to enumerate the repair response too.
        output = self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]
        return FakeResponse(
            parsed_output=output,
            usage=self._usage or FakeUsage(),
            stop_reason=self._stop_reason,
        )

    def parse(self, **kwargs) -> FakeResponse:
        return self._next(kwargs)

    def stream(self, **kwargs) -> FakeStream:
        """The pipeline streams, so the fake has to be a context manager too."""
        return FakeStream(self._next(kwargs))


class FakeAnthropic:
    """Returns the extractions it was given, in order, and records every call."""

    def __init__(
        self,
        *outputs: ExtractedInvoice | None,
        usage: FakeUsage | None = None,
        stop_reason: str = "end_turn",
    ) -> None:
        if not outputs:
            raise ValueError("FakeAnthropic needs at least one output")
        self.messages = FakeMessages(list(outputs), usage, stop_reason)

    @property
    def calls(self) -> list[FakeCall]:
        return self.messages.calls

    @property
    def call_count(self) -> int:
        return len(self.messages.calls)
