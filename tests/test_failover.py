"""End-to-end failover policy via `httpx.MockTransport`. No network."""
from __future__ import annotations

import json

import httpx
import pytest

import bank


@pytest.fixture
def make_bank(state):
    banks = []

    def _factory(providers, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        b = bank.Bank(providers=providers, state=state, client=client)
        banks.append(b)
        return b

    yield _factory
    for b in banks:
        b.close()


def _ok_response(text="ok"):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def test_429_then_failover_to_next_model(monkeypatch, factory, make_bank, state):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[
        factory.model("testprov", "model_a", tier="T2"),
        factory.model("testprov", "model_b", tier="T2"),
    ])

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == "model_a":
            return httpx.Response(429, headers={"retry-after": "5"}, json={"error": "rate"})
        return _ok_response("from-b")

    b = make_bank([p], handler)
    resp = b.chat(messages=[{"role": "user", "content": "hi"}], tier="T2", max_tokens=4)
    assert resp.text == "from-b"
    assert resp.model == "model_b"
    # cooldown set on model_a
    s_a = state.get_state("testprov", "model_a")
    assert s_a.cooldown_until > 0


def test_401_disables_provider(monkeypatch, factory, make_bank, state):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[factory.model("testprov", "model_a", tier="T2")])

    def handler(req):
        return httpx.Response(401, json={"error": "bad key"})

    b = make_bank([p], handler)
    with pytest.raises(bank.BankExhausted):
        b.chat(messages=[{"role": "user", "content": "hi"}], tier="T2", max_tokens=4)
    assert state.is_provider_disabled("testprov") is True


def test_404_disables_model(monkeypatch, factory, make_bank, state):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[
        factory.model("testprov", "stale", tier="T2"),
        factory.model("testprov", "fresh", tier="T2"),
    ])

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == "stale":
            return httpx.Response(404, json={"error": "no such model"})
        return _ok_response()

    b = make_bank([p], handler)
    resp = b.chat(messages=[{"role": "user", "content": "hi"}], tier="T2", max_tokens=4)
    assert resp.model == "fresh"
    assert state.is_model_disabled("testprov", "stale") is True


def test_400_tool_marks_no_tools(monkeypatch, factory, make_bank, state):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[
        factory.model("testprov", "no_tools_model", tier="T2"),
        factory.model("testprov", "tools_model", tier="T2"),
    ])

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == "no_tools_model":
            return httpx.Response(400, json={"error": "tool calls not supported"})
        return _ok_response()

    b = make_bank([p], handler)
    resp = b.chat(
        messages=[{"role": "user", "content": "hi"}],
        tier="T2",
        max_tokens=4,
        tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
    )
    assert resp.model == "tools_model"
    assert state.get_supports_tools("testprov", "no_tools_model") is False


def test_400_json_marks_no_json(monkeypatch, factory, make_bank, state):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[
        factory.model("testprov", "no_json_model", tier="T2"),
        factory.model("testprov", "json_model", tier="T2"),
    ])

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == "no_json_model":
            return httpx.Response(400, json={
                "error": "response_format json_object not supported"
            })
        return _ok_response()

    b = make_bank([p], handler)
    resp = b.chat(
        messages=[{"role": "user", "content": "hi"}],
        tier="T2",
        max_tokens=4,
        response_format={"type": "json_object"},
    )
    assert resp.model == "json_model"
    assert state.get_supports_json("testprov", "no_json_model") is False


def test_5xx_retries_on_next(monkeypatch, factory, make_bank):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[
        factory.model("testprov", "flaky", tier="T2"),
        factory.model("testprov", "good", tier="T2"),
    ])

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == "flaky":
            return httpx.Response(503, json={"error": "upstream"})
        return _ok_response("recovered")

    b = make_bank([p], handler)
    resp = b.chat(messages=[{"role": "user", "content": "hi"}], tier="T2", max_tokens=4)
    assert resp.text == "recovered"


def test_no_eligible_raises_exhausted(monkeypatch, factory, make_bank):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[factory.model("testprov", "only", tier="T1")])

    def handler(req):
        return _ok_response()

    b = make_bank([p], handler)
    with pytest.raises(bank.BankExhausted):
        b.chat(messages=[{"role": "user", "content": "hi"}], tier="T2", max_tokens=4)


def test_response_format_forwarded_in_payload(monkeypatch, factory, make_bank):
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[factory.model("testprov", "m", tier="T2")])
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return _ok_response()

    b = make_bank([p], handler)
    rf = {"type": "json_object"}
    b.chat(
        messages=[{"role": "user", "content": "hi"}],
        tier="T2", max_tokens=4, response_format=rf,
    )
    assert seen["body"]["response_format"] == rf


def test_tool_calls_normalized_in_response(monkeypatch, factory, make_bank):
    """End-to-end: provider returns OpenAI-style tool_calls, _call surfaces
    canonical list via _normalize_tool_calls."""
    monkeypatch.setenv("TEST_KEY", "x")
    p = factory.provider(models=[factory.model("testprov", "m", tier="T2")])

    def handler(req):
        return httpx.Response(200, json={
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_xyz", "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":"NYC"}'},
                }],
            }}],
        })

    b = make_bank([p], handler)
    resp = b.chat(
        messages=[{"role": "user", "content": "weather?"}],
        tier="T2", max_tokens=4,
        tools=[{"type": "function", "function": {"name": "weather", "parameters": {}}}],
    )
    assert resp.tool_calls is not None
    assert resp.tool_calls[0]["function"]["name"] == "weather"
    assert json.loads(resp.tool_calls[0]["function"]["arguments"]) == {"city": "NYC"}
