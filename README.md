# agentSwarm

Pool of free-tier LLM providers selected at call time by capability tier
+ remaining quota. Local-first, single-host, OpenAI-compatible.

Status: Phase 1 (probe), Phase 2 (bank skeleton), Phase 3 (capability
sniff) all complete. 7 providers wired in. See `findings.md` for the
data behind the choices and `roadmap.md` for what's not built yet.

## Quick start

```bash
# 1. Install deps (Python 3.10+)
pip install -r requirements.txt

# 2. Copy env template, fill in API keys you want to use
cp scripts/.env.example scripts/.env
$EDITOR scripts/.env

# 3. Sanity-check the providers you have keys for
python3 scripts/probe.py

# 4. Use the bank
python3 bank.py --tier T2 --prompt "summarize: HTTP/2 vs HTTP/3" --max-tokens 256
```

## Provider signup (no credit card required for the working set)

Fill any subset; bank silently skips providers without keys. Minimum
viable: Cerebras + Groq + OpenRouter.

| Env var                | Provider          | Get key here                                        |
|------------------------|-------------------|-----------------------------------------------------|
| `CEREBRAS_KEY`         | Cerebras          | https://cloud.cerebras.ai/                          |
| `GROQ_KEY`             | Groq              | https://console.groq.com/keys                       |
| `OPENROUTER_KEY`       | OpenRouter        | https://openrouter.ai/settings/keys                 |
| `GOOGLE_AI_STUDIO_KEY` | Google AI Studio  | https://aistudio.google.com/apikey                  |
| `SAMBANOVA_KEY`        | SambaNova         | https://cloud.sambanova.ai/                         |
| `NVIDIA_NIM_KEY`       | NVIDIA NIM        | https://build.nvidia.com/                           |
| `HF_TOKEN`             | HuggingFace       | https://huggingface.co/settings/tokens (Read scope) |
| `DEEPSEEK_KEY`         | DeepSeek          | paid-only as of 2026-05-03 — skip                   |
| `MOONSHOT_KEY`         | Moonshot (Kimi)   | paid-only as of 2026-05-03 — skip                   |

For OpenRouter `:free` slugs to resolve, enable
https://openrouter.ai/settings/privacy → "free models privacy" toggle.
Without it every `:free` call returns 404.

## What it does

`bank.py` exposes a `Bank` class:

```python
from bank import Bank
bank = Bank.from_yaml("scripts/providers.yaml", state="bank-state.sqlite")

# Non-streaming
resp = bank.chat(
    messages=[{"role": "user", "content": "hello"}],
    tier="T2",                         # T1 reasoning / T2 general / T3 fast
    max_tokens=256,
    prefer=["groq"],                   # provider preference
    prefer_models=["llama-3.3-70b"],   # specific model preference
    avoid=["nvidia_nim"],
    avoid_models=["..."],
)
print(resp.text, resp.provider, resp.model, resp.latency_ms)
print(resp.meta)  # prompt_tokens, completion_tokens, rpm_remaining, etc.

# Streaming (OpenAI-compat SSE)
for chunk in bank.stream(messages=[...], tier="T2", max_tokens=512):
    if isinstance(chunk, str):
        print(chunk, end="", flush=True)
    else:
        # final dict: {provider, model, latency_ms, ttfb_ms, meta}
        pass

# Refresh provider catalogs from /v1/models, auto-disable stale IDs
report = bank.discover_live_models()
```

Selection policy lives in `TIER_PRIORITY` at the top of `bank.py` plus
the `_score()` method. State persists in `bank-state.sqlite` (WAL).
Failover on 429/5xx/timeout, permanent disable on 404/410.

## Tier rosters (post Phase-3 capsniff)

- **T1 reasoning** (15 verified): groq/gpt-oss-120b, sambanova {V3.2, V3.1, gpt-oss-120b}, nim {kimi-k2-instruct, gpt-oss-120b, nemotron-super-49b, glm5}, openrouter {gpt-oss-120b, nemotron-3-super, nemotron-nano-omni-reasoning, glm-4.5-air}, hf {DeepSeek-R1, DeepSeek-V3.2, Qwen3-235B}.
- **T2 general** (10): groq {llama-3.3-70b, qwen3-32b, llama-4-scout, gpt-oss-20b}, google/gemini-2.5-flash, sambanova/Llama-4-Maverick, nim/qwen3-next-80b, openrouter {gpt-oss-20b, minimax-m2.5}, hf {Llama-3.3-70B, Qwen3-Next-80B}.
- **T3 fast** (3): groq/llama-3.1-8b-instant, cerebras/llama3.1-8b, hf/gemma-3-27b-it.

Re-verify any time:
```bash
python3 scripts/capsniff.py --tier T1
```

## Layout

```
agentSwarm/
  bank.py                    # main module: Bank, BankResponse, state, headers
  bank-design.md             # architecture + decisions
  findings.md                # phase-1 probe + phase-3 capsniff verdicts
  roadmap.md                 # 6 optional follow-on phases
  research-plan.md           # original 5-phase plan
  README.md                  # this file
  requirements.txt           # pip deps
  Free Tier LLM Aggregation Strategy.txt  # source notes (read-only)
  providers/<name>.md        # one note per provider, post-probe status
  scripts/
    .env.example             # env-var slots
    .env                     # your keys (gitignored)
    providers.yaml           # provider registry: base_url, key_env, models, tiers
    probe.py                 # phase-1 connectivity + header capture
    probe-spec.md            # what probe.py does
    capsniff.py              # phase-3 capability sniff (one reasoning + one code prompt per model)
  findings/                  # probe + capsniff JSON output (gitignored)
  bank-state.sqlite          # live state (gitignored)
```

## CLI cheat sheet

```bash
# Connectivity probe (all providers with keys)
python3 scripts/probe.py
python3 scripts/probe.py groq cerebras --retries 1

# Capability sniff
python3 scripts/capsniff.py --tier T1     # T1 / T2 / T3
python3 scripts/capsniff.py groq           # single provider

# Bank one-shot
python3 bank.py --tier T2 --prompt "..." --max-tokens 256
python3 bank.py --tier T1 --prefer huggingface --max-tokens 1024
python3 bank.py --tier T3 --stream --prompt "count to 5" --max-tokens 32

# Refresh provider catalogs (auto-disable stale model IDs)
python3 bank.py --discover

# Pin a single model
python3 bank.py --tier any --prefer-models deepseek-ai/DeepSeek-R1 --prompt "..."
```

## Troubleshooting

- **All providers skipped on probe** — `.env` not in `scripts/`. `cp scripts/.env.example scripts/.env`.
- **`BankExhausted: no eligible (provider, model) for tier=...`** — every model in that tier is in cooldown or disabled. Check: `sqlite3 bank-state.sqlite "SELECT * FROM disabled_models"` and `SELECT provider, model_id, cooldown_until FROM live`.
- **OpenRouter `:free` returns 404** — privacy toggle off. Enable at https://openrouter.ai/settings/privacy.
- **NVIDIA NIM ReadTimeout** — first call cold-starts a container. `request_timeout: 120` already set in yaml; second call usually warm. Bank automatically failovers after timeout.
- **SambaNova exhausted fast** — 20 RPD per model on free tier; the bank deprioritizes it correctly. Check `SELECT * FROM live WHERE provider='sambanova'`.
- **Stale catalog** — run `python3 bank.py --discover` then check `disabled_models` table for fresh entries.

## License / sharing

This codebase is shared as-is for review and internal use. No license
declared — ask the original author before redistributing.
