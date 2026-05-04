# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**agentSwarm** is a free-tier LLM provider aggregation library (`bank.py`) that routes requests across 7 providers and 40+ models, selecting by capability tier (T1/T2/T3), remaining quota, and failover state. It exposes a unified OpenAI-compatible interface with automatic failover, quota tracking, and SQLite-backed state persistence.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# CLI — single chat turn
python3 bank.py --tier T2 --prompt "hello" --max-tokens 256

# CLI — discover/refresh live models
python3 bank.py --discover

# Research scripts
python3 scripts/probe.py [provider_names]     # connectivity + header probe
python3 scripts/capsniff.py --tier T1         # capability sniff (reasoning + code)
```

No build step, no test suite, no linter configured yet (planned in roadmap phase 6).

## Environment Setup

Copy `scripts/.env.example` to `scripts/.env` and populate API keys. The `Bank` class loads these via `python-dotenv`. The `.env` file is gitignored.

## Architecture

### Core Module: `bank.py`

Single-file library (~950 lines). Three main classes:

- **`Bank`** — public API. `Bank.from_yaml(providers_yaml, state=sqlite_path)` initializes everything. Primary methods: `.chat()`, `.stream()`, `.discover_live_models()`.
- **`StateStore`** — SQLite WAL persistence (tables: `providers`, `models`, `live`, `events`, `disabled_models`). Tracks per-(provider, model) quotas, cooldowns, and permanent disables.
- **`BankResponse`** — return type: `text`, `provider`, `model`, `latency_ms`, `meta` dict (token counts, rpm/rpd remaining).

### Provider Registry: `scripts/providers.yaml`

7 providers, 40+ models. Each model entry specifies tier (T1/T2/T3), endpoint, context window, and rate limits. The YAML is the single source of truth for what's in the pool.

### Selection & Failover Policy

1. `Bank.acquire()` filters candidates by requested tier, scores by quota remaining, and respects `prefer`/`avoid` lists.
2. `Bank._call()` executes the HTTP request (OAI-compat `/chat/completions`), parses rate-limit headers, updates `StateStore`.
3. On 429 → cooldown + retry next candidate (up to `max_failovers=6`). On 5xx → exponential backoff. On 401/403 → disable provider permanently. On 404/410 → disable model permanently.

### Quota Tracking

- **Header-exposed providers** (Groq, Cerebras, HuggingFace, OpenRouter): parse `x-ratelimit-*` response headers.
- **Opaque providers** (Google AI Studio, NVIDIA NIM): local token-bucket from documented limits; 429 is authoritative.
- **OpenRouter `:free`**: account-level tracking (20 RPM / 50 RPD shared across all free models).

### Tier Roster

| Tier | Priority | Key providers |
|------|----------|---------------|
| T1 (reasoning) | quality-first | HuggingFace → NVIDIA NIM → SambaNova → OpenRouter |
| T2 (general) | quota-first | Groq → HuggingFace → SambaNova → Google AI Studio → NVIDIA NIM → OpenRouter |
| T3 (fast) | latency-first | Groq → Cerebras → Google AI Studio → SambaNova → HuggingFace → OpenRouter |

### Key Design Docs

- `bank-design.md` — selection policy, data model, quota strategies, failover rules, cooldown defaults
- `findings.md` — Phase 1–3 research verdicts, per-provider notes, tier composition rationale
- `roadmap.md` — 6 planned follow-on phases (latency-aware T3, weekly quota cron, multi-process safety, tool calling, cost accounting, package layout)

## Public API

```python
from bank import Bank, BankResponse, BankExhausted

bank = Bank.from_yaml("scripts/providers.yaml", state="bank-state.sqlite")

# Non-streaming
resp = bank.chat(
    messages=[{"role": "user", "content": "hello"}],
    tier="T2",                          # "T1" | "T2" | "T3" | None
    max_tokens=256,
    prefer=["groq"],
    avoid=["nvidia_nim"],
    max_failovers=6,
)
# resp.text, resp.provider, resp.model, resp.latency_ms, resp.meta

# Streaming
for chunk in bank.stream(messages=[...], tier="T2", max_tokens=512):
    if isinstance(chunk, str):
        print(chunk, end="", flush=True)
    else:
        # final summary dict: provider, model, latency_ms
        pass
```
