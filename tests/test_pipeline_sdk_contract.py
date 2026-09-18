"""The real Anthropic SDK, driven through a mock HTTP transport.

Every other pipeline test uses a fake client, which proves the pipeline's logic
but not that it speaks to the SDK correctly: a wrong parameter name, a format
that does not merge with effort, or a streamed response that never parses into
the schema would all pass the fake and fail on the first paid request.

Here the genuine `anthropic.Anthropic` client builds and sends the request, and a
mock transport answers with a recorded-shape server-sent-event stream. Nothing
leaves the machine and nothing is billed, but the request body asserted on below
is byte-for-byte what the API would receive.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from peppol_e_invoice_intake.corpus.catalogue import CATALOGUE
from peppol_e_invoice_intake.pipeline import Budget
from peppol_e_invoice_intake.pipeline.extraction import extract
from peppol_e_invoice_intake.pipeline.request import ModelRequestError
from perfect_extraction import perfect_extraction

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

KEY = "standard-single-rate"


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


def sse_stream(
    text: str,
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 3_000,
    output_tokens: int = 1_200,
    cache_read: int = 700,
) -> bytes:
    """A streamed Messages response: a thinking block, then the JSON as text."""
    events = [
        _event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": cache_read,
                    },
                },
            },
        ),
        # Adaptive thinking is on, and on Opus 5 its text is omitted by default: the
        # block arrives empty with only a signature. Parsing must skip past it.
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "c2lnbmF0dXJl"},
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
    ]
    # Split the JSON across several deltas, as a real stream would.
    for start in range(0, len(text), 400):
        events.append(
            _event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": text[start : start + 400]},
                },
            )
        )
    events += [
        _event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]
    return "".join(events).encode("utf-8")


class Recorder:
    """A mock transport that answers every request and keeps the request bodies."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[dict] = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=self.body
        )

    def client(self):
        return anthropic.Anthropic(
            api_key="sk-ant-test-not-a-real-key",
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self)),
        )


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    document = tmp_path / "invoice.pdf"
    document.write_bytes(b"%PDF-1.4\nplaceholder\n")
    return document


@pytest.fixture
def expected():
    return perfect_extraction(CATALOGUE[KEY])


def run_extract(pdf: Path, recorder: Recorder, **kwargs):
    return extract(
        pdf, budget=Budget(limit_usd=Decimal("1.00")), client=recorder.client(), **kwargs
    )


def test_a_streamed_structured_response_parses_into_the_schema(pdf, expected):
    recorder = Recorder(sse_stream(expected.model_dump_json()))
    result = run_extract(pdf, recorder)
    assert result.invoice == expected


def test_usage_from_the_stream_is_what_gets_billed(pdf, expected):
    recorder = Recorder(sse_stream(expected.model_dump_json()))
    result = run_extract(pdf, recorder)

    assert result.spend.input_tokens == 3_000
    assert result.spend.output_tokens == 1_200
    assert result.spend.cache_read_tokens == 700
    # 3000 x $5 + 1200 x $25 + 700 x $0.50, per million
    assert result.spend.usd == Decimal("0.04535")


def test_the_request_asks_for_low_effort_and_the_schema_together(pdf, expected):
    """Both have to arrive in one `output_config`. If the schema displaced the
    effort, the request would silently fall back to the default effort and the run
    would cost several times what it should - with nothing failing to say so."""
    recorder = Recorder(sse_stream(expected.model_dump_json()))
    run_extract(pdf, recorder)

    config = recorder.requests[0]["output_config"]
    assert config["effort"] == "low"
    assert config["format"]["type"] == "json_schema"
    assert "lines" in config["format"]["schema"]["properties"]


def test_the_request_streams_with_adaptive_thinking(pdf, expected):
    recorder = Recorder(sse_stream(expected.model_dump_json()))
    run_extract(pdf, recorder)

    body = recorder.requests[0]
    assert body["stream"] is True
    assert body["thinking"] == {"type": "adaptive"}
    assert body["model"] == "claude-opus-5"
    assert body["max_tokens"] == 32_000


def test_the_system_prompt_is_marked_for_caching(pdf, expected):
    recorder = Recorder(sse_stream(expected.model_dump_json()))
    run_extract(pdf, recorder)
    assert recorder.requests[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_the_pdf_is_sent_as_a_base64_document_block_before_the_instruction(pdf, expected):
    recorder = Recorder(sse_stream(expected.model_dump_json()))
    run_extract(pdf, recorder)

    content = recorder.requests[0]["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert "\n" not in content[0]["source"]["data"]
    assert content[1]["type"] == "text"


def test_no_effort_leaves_output_config_holding_only_the_schema(pdf, expected):
    recorder = Recorder(sse_stream(expected.model_dump_json()))
    run_extract(pdf, recorder, effort=None)
    assert set(recorder.requests[0]["output_config"]) == {"format"}


def test_a_truncated_stream_is_rejected_rather_than_parsed(pdf, expected):
    """The bug this file was written to catch: letting the SDK parse during the
    stream raised a pydantic error on truncated JSON, before the stop reason had
    arrived, and crashed the run instead of reporting the document."""
    text = expected.model_dump_json()
    recorder = Recorder(sse_stream(text[: len(text) // 2], stop_reason="max_tokens"))
    with pytest.raises(ModelRequestError, match="truncated"):
        run_extract(pdf, recorder)


def test_a_truncated_response_is_still_charged_to_the_budget(pdf, expected):
    """It was billed, so it counts. A budget that only records successes
    undercounts precisely when something is going wrong."""
    text = expected.model_dump_json()
    recorder = Recorder(
        sse_stream(text[:300], stop_reason="max_tokens", output_tokens=32_000)
    )
    budget = Budget(limit_usd=Decimal("5.00"))
    with pytest.raises(ModelRequestError):
        extract(pdf, budget=budget, client=recorder.client())

    assert budget.requests == 1
    assert budget.spent_usd > Decimal("0.80")  # 32k output tokens at $25 per million


def test_json_that_does_not_match_the_schema_is_an_error_not_a_crash(pdf):
    recorder = Recorder(sse_stream('{"invoice_number": 42}'))
    budget = Budget(limit_usd=Decimal("1.00"))
    with pytest.raises(ModelRequestError, match="did not match"):
        extract(pdf, budget=budget, client=recorder.client())
    assert budget.requests == 1
