"""MCP stdio server exposing bank.py to Claude Code as opt-in delegation.

Three tools: llm_call (delegate a cheap subtask to a free-tier LLM,
optionally over a local file via `file_path`), bank_status (read-only
health/quota snapshot), discover_models (refresh /v1/models lists).
Run as: python mcp_server.py.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from bank import Bank, BankError, BankExhausted

REPO = Path(__file__).resolve().parent
YAML_PATH = Path(os.getenv("BANK_PROVIDERS_YAML", REPO / "scripts" / "providers.yaml"))
STATE_PATH = Path(os.getenv("BANK_STATE_PATH", REPO / "bank-state.sqlite"))

# Captured at import — later os.chdir() must not move the security boundary.
BASE_DIR = Path.cwd().resolve()

DEFAULT_SYSTEM_FOR_FILE = (
    "You are a focused assistant. The user will give you an instruction "
    "followed by the contents of a file. Apply the instruction to the file "
    "contents. Do not invent details that are not in the file. Be concise."
)

BANK = Bank.from_yaml(YAML_PATH, state=STATE_PATH)
mcp = FastMCP("agentswarm-bank")


def _resolve_under_cwd(path: str) -> Path:
    """Resolve `path` and verify it stays inside BASE_DIR."""
    p = Path(path)
    resolved = (p if p.is_absolute() else (BASE_DIR / p)).resolve()
    if not resolved.is_relative_to(BASE_DIR):
        raise ValueError(f"path outside server root: {path}")
    return resolved


@mcp.tool()
def llm_call(
    prompt: str,
    system: str | None = None,
    tier: str = "T2",
    max_tokens: int = 512,
    prefer: list[str] | None = None,
    avoid: list[str] | None = None,
    file_path: str | None = None,
    max_bytes: int = 200_000,
) -> dict:
    """Delegate one self-contained subtask to a free-tier LLM.

    USE FOR cheap, low-stakes work where a wrong answer is easy to spot
    and discard: file/diff summarization, docstring drafts, rename
    suggestions, mechanical text transforms, regex generation, field
    extraction. One prompt in, one answer out — the model has no repo
    context beyond what is in `prompt` (and the file at `file_path`,
    if given).

    file_path (TOKEN SAVER): if set, the MCP server reads this file
    from disk and appends its contents to your prompt before calling
    the model. PREFER THIS over reading the file yourself and pasting
    contents into `prompt` — it skips paying for the file's tokens on
    the main model. Path is resolved against the MCP server's working
    directory; absolute paths or `..` traversal outside that root are
    rejected. Files larger than max_bytes are truncated.

    DO NOT USE for multi-step reasoning, code committed without review,
    architectural decisions, or tasks needing follow-up turns. Do those
    yourself.

    Tiers: T2 (general, default) | T3 (fast/cheap) | T1 (reasoning,
    only when genuinely needed). Returns text + provider/model/latency,
    plus a `file` key when `file_path` was set.
    """
    file_info: dict | None = None
    user_content = prompt
    if file_path is not None:
        resolved = _resolve_under_cwd(file_path)
        rel = resolved.relative_to(BASE_DIR).as_posix()
        with open(resolved, "rb") as fh:
            raw = fh.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        file_text = raw.decode("utf-8", errors="replace")
        file_info = {"path": rel, "bytes": len(raw), "truncated": truncated}
        if len(raw) == 0:
            return {
                "text": "",
                "provider": None,
                "model": None,
                "latency_ms": 0,
                "tier": tier,
                "tokens": {"prompt": 0, "completion": 0, "total": 0},
                "file": file_info,
            }
        user_content = f"{prompt}\n\n--- FILE: {rel} ---\n{file_text}"

    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    elif file_path is not None:
        msgs.append({"role": "system", "content": DEFAULT_SYSTEM_FOR_FILE})
    msgs.append({"role": "user", "content": user_content})
    try:
        r = BANK.chat(
            messages=msgs,
            tier=tier,
            max_tokens=max_tokens,
            prefer=prefer,
            avoid=avoid,
        )
    except BankExhausted as e:
        raise RuntimeError(f"bank exhausted: {e}")
    except BankError as e:
        raise RuntimeError(f"bank error: {e}")
    out = {
        "text": r.text,
        "provider": r.provider,
        "model": r.model,
        "latency_ms": r.latency_ms,
        "tier": r.meta.get("tier"),
        "tokens": {
            "prompt": r.meta.get("prompt_tokens"),
            "completion": r.meta.get("completion_tokens"),
            "total": r.meta.get("total_tokens"),
        },
    }
    if file_info is not None:
        out["file"] = file_info
    return out


@mcp.tool()
def bank_status() -> dict:
    """Read-only snapshot: per-provider health, disabled models,
    cooldown remaining, latency p50, recent quota remaining. Use to
    debug delegation failures."""
    now = time.time()
    out: dict = {"providers": []}
    for name, p in BANK.providers.items():
        models = []
        for m in p.models:
            key = "*" if p.shared_bucket else m.model_id
            s = BANK.state.get_state(name, key)
            doc = BANK.state.get_doc_limits(name, m.model_id)
            models.append({
                "id": m.model_id,
                "tier": m.tier,
                "disabled": BANK.state.is_model_disabled(name, m.model_id),
                "supports_tools": BANK.state.get_supports_tools(name, m.model_id),
                "supports_json": BANK.state.get_supports_json(name, m.model_id),
                "cooldown_remaining_s": max(0.0, s.cooldown_until - now),
                "latency_p50_ms": s.latency_p50_ms,
                "doc_rpm": doc["doc_rpm"],
                "doc_rpd": doc["doc_rpd"],
                "doc_tpm": doc["doc_tpm"],
                "doc_tpd": doc["doc_tpd"],
                "rpm_remaining": s.rpm_remaining,
                "rpd_remaining": s.rpd_remaining,
                "tpm_remaining": s.tpm_remaining,
                "tpd_remaining": s.tpd_remaining,
                "tpm_limit": s.tpm_limit,
                "tpd_limit": s.tpd_limit,
                "rpm_limit": s.rpm_limit,
                "rpd_limit": s.rpd_limit,
                "benchmarks": m.benchmarks,
            })
        out["providers"].append({
            "name": name,
            "has_key": bool(os.getenv(p.key_env)),
            "disabled": BANK.state.is_provider_disabled(name),
            "models": models,
        })
    return out


@mcp.tool()
def discover_models(only: list[str] | None = None) -> dict:
    """Refresh provider /v1/models lists; auto-disable models that
    disappeared upstream. Slow (network) — call sparingly."""
    try:
        return BANK.discover_live_models(only=only)
    except BankError as e:
        raise RuntimeError(f"discover failed: {e}")


if __name__ == "__main__":
    mcp.run()
