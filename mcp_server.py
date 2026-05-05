"""MCP stdio server exposing bank.py to Claude Code as opt-in delegation.

Three tools: llm_call (delegate a cheap subtask to a free-tier LLM),
bank_status (read-only health/quota snapshot), discover_models
(refresh /v1/models lists). Run as: python mcp_server.py.
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

BANK = Bank.from_yaml(YAML_PATH, state=STATE_PATH)
mcp = FastMCP("agentswarm-bank")


@mcp.tool()
def llm_call(
    prompt: str,
    system: str | None = None,
    tier: str = "T2",
    max_tokens: int = 512,
    prefer: list[str] | None = None,
    avoid: list[str] | None = None,
) -> dict:
    """Delegate one self-contained subtask to a free-tier LLM.

    USE FOR cheap, low-stakes work where a wrong answer is easy to spot
    and discard: file/diff summarization, docstring drafts, rename
    suggestions, mechanical text transforms, regex generation, field
    extraction. One prompt in, one answer out — the model has no repo
    context beyond what is in `prompt`.

    DO NOT USE for multi-step reasoning, code committed without review,
    architectural decisions, or tasks needing follow-up turns. Do those
    yourself.

    Tiers: T2 (general, default) | T3 (fast/cheap) | T1 (reasoning,
    only when genuinely needed). Returns text + provider/model/latency.
    """
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
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
    return {
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
            models.append({
                "id": m.model_id,
                "tier": m.tier,
                "disabled": BANK.state.is_model_disabled(name, m.model_id),
                "supports_tools": BANK.state.get_supports_tools(name, m.model_id),
                "supports_json": BANK.state.get_supports_json(name, m.model_id),
                "cooldown_remaining_s": max(0.0, s.cooldown_until - now),
                "latency_p50_ms": s.latency_p50_ms,
                "rpm_remaining": s.rpm_remaining,
                "rpd_remaining": s.rpd_remaining,
                "tpm_remaining": s.tpm_remaining,
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
