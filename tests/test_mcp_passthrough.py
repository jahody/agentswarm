"""mcp_server.llm_call file-pass-through mode. No network, no bank state."""
from __future__ import annotations

from pathlib import Path

import pytest

import mcp_server


@pytest.fixture
def fake_bank(monkeypatch):
    """Replace BANK.chat with a recorder. Returns a list of recorded calls."""
    calls: list[dict] = []

    class _Resp:
        text = "stub answer"
        provider = "stub_provider"
        model = "stub_model"
        latency_ms = 12
        meta = {"tier": "T2", "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return _Resp()

    monkeypatch.setattr(mcp_server.BANK, "chat", fake_chat)
    return calls


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    """Pin BASE_DIR to tmp_path for the duration of a test."""
    monkeypatch.setattr(mcp_server, "BASE_DIR", tmp_path.resolve())
    return tmp_path


def test_file_path_reads_and_appends_to_user_message(in_tmp, fake_bank):
    body = b"file body line one\nfile body line two\n"
    (in_tmp / "hello.txt").write_bytes(body)

    out = mcp_server.llm_call(prompt="summarize", file_path="hello.txt", tier="T3")

    assert len(fake_bank) == 1
    sent = fake_bank[0]["messages"]
    # System prompt added in file mode
    assert sent[0]["role"] == "system"
    assert "instruction" in sent[0]["content"].lower()
    # User message contains both instruction and file body
    assert sent[1]["role"] == "user"
    assert sent[1]["content"].startswith("summarize\n\n--- FILE: hello.txt ---\n")
    assert "file body line one" in sent[1]["content"]
    # Response carries the file metadata
    assert out["file"] == {"path": "hello.txt", "bytes": len(body), "truncated": False}
    assert out["text"] == "stub answer"


def test_path_traversal_blocked(in_tmp, fake_bank):
    with pytest.raises(ValueError, match="path outside server root"):
        mcp_server.llm_call(prompt="x", file_path="../../etc/passwd")
    assert fake_bank == []


def test_absolute_path_outside_cwd_blocked(in_tmp, fake_bank, tmp_path):
    # A real absolute path that resolves outside BASE_DIR. tmp_path.parent is
    # pytest's session root, guaranteed to be a parent of (and thus outside)
    # tmp_path itself.
    outside = (tmp_path.parent / "definitely_outside.txt")
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="path outside server root"):
        mcp_server.llm_call(prompt="x", file_path=str(outside))
    assert fake_bank == []


def test_truncation_at_max_bytes(in_tmp, fake_bank):
    body = "A" * 500
    (in_tmp / "big.txt").write_text(body, encoding="utf-8")

    out = mcp_server.llm_call(prompt="head", file_path="big.txt", max_bytes=100)

    assert out["file"]["truncated"] is True
    assert out["file"]["bytes"] == 100
    sent_user = fake_bank[0]["messages"][-1]["content"]
    # The body slice in the user message is exactly 100 'A's
    assert sent_user.endswith("A" * 100)
    assert "A" * 101 not in sent_user


def test_no_file_path_keeps_legacy_behavior(in_tmp, fake_bank):
    out = mcp_server.llm_call(prompt="hello", tier="T2")

    sent = fake_bank[0]["messages"]
    # No system message when neither system= nor file_path= is set
    assert len(sent) == 1
    assert sent[0] == {"role": "user", "content": "hello"}
    assert "file" not in out


def test_empty_file_short_circuits(in_tmp, fake_bank):
    (in_tmp / "empty.txt").write_text("", encoding="utf-8")

    out = mcp_server.llm_call(prompt="x", file_path="empty.txt")

    assert out["text"] == ""
    assert out["file"] == {"path": "empty.txt", "bytes": 0, "truncated": False}
    assert fake_bank == []  # Bank not called for empty file
