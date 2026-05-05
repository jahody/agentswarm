"""Streaming SSE — text deltas, tool-call accumulation, final dict shape."""
from __future__ import annotations

import json

import httpx
import pytest

import bank


def _sse(*chunks: dict | str) -> bytes:
    """Build an OpenAI-compat SSE byte body from a list of dicts (or [DONE])."""
    lines = []
    for c in chunks:
        if c == "[DONE]":
            lines.append("data: [DONE]")
        else:
            lines.append("data: " + json.dumps(c))
        lines.append("")  # blank line terminates each event
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def make_bank(state):
    banks = []

    def _factory(providers, sse_body, status=200):
        def handler(req):
            return httpx.Response(
                status,
                content=sse_body,
                headers={"content-type": "text/event-stream"},
            )
        client = httpx.Client(transport=httpx.MockTransport(handler))
        b = bank.Bank(providers=providers, state=state, client=client)
        banks.append(b)
        return b

    yield _factory
    for b in banks:
        b.close()


def test_stream_yields_text_pieces_then_final(monkeypatch, factory, make_bank):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[factory.model("testprov", "m", tier="T2")])
    body = _sse(
        {"choices": [{"delta": {"content": "hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        "[DONE]",
    )
    b = make_bank([p], body)
    out = list(b.stream(
        messages=[{"role": "user", "content": "hi"}], tier="T2", max_tokens=4
    ))
    text = "".join(c for c in out if isinstance(c, str))
    final = next(c for c in out if isinstance(c, dict))
    assert text == "hello"
    assert final["provider"] == "testprov"
    assert final["tool_calls"] is None


def test_stream_accumulates_tool_calls(monkeypatch, factory, make_bank):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[factory.model("testprov", "m", tier="T2")])
    body = _sse(
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "weather", "arguments": "{\"ci"},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": "ty\":\"Paris"},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": "\"}"},
        }]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    )
    b = make_bank([p], body)
    out = list(b.stream(
        messages=[{"role": "user", "content": "weather?"}],
        tier="T2", max_tokens=4,
        tools=[{"type": "function", "function": {"name": "weather", "parameters": {}}}],
    ))
    final = next(c for c in out if isinstance(c, dict))
    assert final["tool_calls"] is not None
    tc = final["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Paris"}


def test_stream_multiple_tool_calls_by_index(monkeypatch, factory, make_bank):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[factory.model("testprov", "m", tier="T2")])
    body = _sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c0", "function": {"name": "a", "arguments": "{}"}},
            {"index": 1, "id": "c1", "function": {"name": "b", "arguments": "{}"}},
        ]}}]},
        "[DONE]",
    )
    b = make_bank([p], body)
    out = list(b.stream(
        messages=[{"role": "user", "content": "x"}], tier="T2", max_tokens=4,
        tools=[{"type": "function", "function": {"name": "a", "parameters": {}}}],
    ))
    final = next(c for c in out if isinstance(c, dict))
    assert final["tool_calls"] is not None
    assert {tc["function"]["name"] for tc in final["tool_calls"]} == {"a", "b"}


def test_stream_429_failover(monkeypatch, factory, state):
    """Mid-failover behavior: first model 429s before first byte, second works."""
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[
        factory.model("testprov", "blocked", tier="T2"),
        factory.model("testprov", "ok", tier="T2"),
    ])

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == "blocked":
            return httpx.Response(429, headers={"retry-after": "5"}, json={"error": "rate"})
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "hi"}}]},
                "[DONE]",
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    b = bank.Bank(providers=[p], state=state, client=client)
    try:
        out = list(b.stream(
            messages=[{"role": "user", "content": "hi"}], tier="T2", max_tokens=4
        ))
    finally:
        b.close()
    text = "".join(c for c in out if isinstance(c, str))
    final = next(c for c in out if isinstance(c, dict))
    assert text == "hi"
    assert final["model"] == "ok"
