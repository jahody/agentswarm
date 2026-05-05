"""`_normalize_tool_calls` — provider variations to canonical OpenAI list."""
from __future__ import annotations

import json

import bank


def test_passthrough_openai_shape():
    msg = {
        "tool_calls": [
            {"id": "call_a", "type": "function",
             "function": {"name": "weather", "arguments": '{"city":"Paris"}'}},
        ]
    }
    out = bank._normalize_tool_calls(msg)
    assert out is not None and len(out) == 1
    assert out[0]["id"] == "call_a"
    assert out[0]["function"]["name"] == "weather"
    assert json.loads(out[0]["function"]["arguments"]) == {"city": "Paris"}


def test_legacy_function_call_wrapped():
    msg = {"function_call": {"name": "weather", "arguments": '{"city":"Lyon"}'}}
    out = bank._normalize_tool_calls(msg)
    assert out is not None and len(out) == 1
    assert out[0]["function"]["name"] == "weather"
    assert out[0]["id"].startswith("call_")
    assert out[0]["type"] == "function"


def test_dict_arguments_serialized():
    # Some providers may emit pre-parsed args.
    msg = {"tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": {"k": 1}}}]}
    out = bank._normalize_tool_calls(msg)
    assert out is not None
    assert json.loads(out[0]["function"]["arguments"]) == {"k": 1}


def test_missing_tool_calls_returns_none():
    assert bank._normalize_tool_calls({"content": "hi"}) is None
    assert bank._normalize_tool_calls({"tool_calls": []}) is None
    assert bank._normalize_tool_calls({"tool_calls": None}) is None


def test_synthesizes_id_when_absent():
    msg = {"tool_calls": [{"function": {"name": "weather", "arguments": "{}"}}]}
    out = bank._normalize_tool_calls(msg)
    assert out and out[0]["id"] == "call_0"


def test_skips_malformed_entries():
    msg = {
        "tool_calls": [
            {"function": {"name": "ok", "arguments": "{}"}},
            "garbage",                                # not a dict
            {"function": {"arguments": "{}"}},        # no name
        ]
    }
    out = bank._normalize_tool_calls(msg)
    assert out is not None and len(out) == 1
    assert out[0]["function"]["name"] == "ok"


def test_arguments_default_empty_object():
    msg = {"tool_calls": [{"function": {"name": "f"}}]}
    out = bank._normalize_tool_calls(msg)
    assert out and out[0]["function"]["arguments"] == "{}"
