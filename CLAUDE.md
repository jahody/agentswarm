# CLAUDE.md

**agentSwarm** — free-tier LLM aggregation library (`bank.py`). Routes across 7 providers / 30+ models by tier (T1/T2/T3), quota, and failover state. Unified OAI-compatible interface; automatic failover, quota tracking, SQLite persistence.

## Commands

```bash
pip install -r requirements.txt
python3 bank.py --tier T2 --prompt "hello" --max-tokens 256
python3 bank.py --discover
python3 bank.py --status
```

Env: `scripts/.env.example` -> `scripts/.env` (gitignored; `python-dotenv`).

## Architecture

**`bank.py`** — `Bank`: public API (`.chat()`, `.stream()`, `.discover_live_models()`). `StateStore`: SQLite WAL, 5 tables, tracks quotas/cooldowns/latency p50/tool support.

**`scripts/providers.yaml`** — 7 providers, 40+ models with tier/endpoint/context/rate-limits.

### Selection & Failover

`acquire()` filters by tier, scores by quota, respects `prefer`/`avoid`. T3 scores by `-latency_p50_ms`. Skips `supports_tools=false` when tools requested. `_call()` posts to `/chat/completions`, parses headers, updates `StateStore`.

429→cooldown+next (max 6). 5xx→backoff. 401/403→disable provider. 404/410→disable model. 400+"tool"→`supports_tools=false`+failover.

**Tiers**: T1=reasoning/quality-first, T2=general/quota-first, T3=fast/latency-p50 (Groq/Cerebras win).

## Delegating to Free-Tier LLMs

MCP server `agentswarm-bank`: `llm_call(prompt, system?, tier?, max_tokens?, prefer?, avoid?, file_path?, max_bytes?)`, `bank_status()`, `discover_models(only?)`.

**Use for**: summaries, docstring drafts, transforms, regex, field extraction. Default `tier="T2"`, `"T3"` for speed, `"T1"` for reasoning only. **Pass `file_path`** when the task is over a local file — the server reads it (CWD-restricted) so you don't pay tokens to inline the contents. **Do NOT**: multi-step work, unreviewed code commits, tasks needing follow-up turns.

## Browser port

A static JS port of the router runs on the site: `jahody.github.io/public/apps/agentswarm/` (`providers.js` mirrors `scripts/providers.yaml`; `bank.js` ports `_score`/`acquire`/`stream` and the failover table). It covers only the providers that send CORS headers (not SambaNova or NVIDIA NIM). When `providers.yaml`, `TIER_PRIORITY` or the error handling changes, update the port too.
