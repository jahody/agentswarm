# Free-Tier Agent Bank: Research Plan

## Goal

Build local "agent bank": pool of free-tier LLM providers, picked at call time by:
1. Liveness (key still valid).
2. Remaining quota (RPM, RPD, TPM, total tokens).
3. Capability tier (reasoning / general / fast).

Source doc: `Free Tier LLM Aggregation Strategy.txt` (in this folder).

## Out of scope for now

- Multi-account pooling (TOS gray zone, detection risk). Single account per provider.
- Web reverse-engineered endpoints (Duck.ai etc.). Fragile.
- On-prem inference. Hardware not available.

## Deliverables (this folder)

```
agentSwarm/
  research-plan.md                # this file
  providers/                      # one .md per provider, filled during testing
    google-ai-studio.md
    deepseek.md
    moonshot-kimi.md
    cerebras.md
    sambanova.md
    nvidia-nim.md
    groq.md
    openrouter.md
    huggingface.md
  findings.md                     # final summary table + verdict per provider
  scripts/
    probe.py                      # connectivity + header inspection probe
    quota_drain.py                # optional: confirm 429 behavior
    .env.example                  # API key slots, no secrets committed
  bank-design.md                  # bank architecture once research done
```

## Candidate providers

Filtered to "real free tier, no credit card, OpenAI-compatible API where possible":

| # | Provider           | Model family               | OAI compat | Notes                                       |
|---|--------------------|----------------------------|------------|---------------------------------------------|
| 1 | Google AI Studio   | Gemini 2.5 / 3.x           | partial    | Native Gemini API or OAI-compat endpoint    |
| 2 | DeepSeek           | V3/V4/R1                   | yes        | 5M signup tokens, pay-as-you-go after       |
| 3 | Moonshot AI (Kimi) | K2.x                       | yes        | $5 voucher, also via NVIDIA NIM             |
| 4 | Cerebras Cloud     | Llama 3.3, Qwen, R1        | yes        | 1M tokens/day free, very fast               |
| 5 | SambaNova Cloud    | Llama 4, DeepSeek          | yes        | $5 credits, no CC                           |
| 6 | NVIDIA NIM         | 80+ models incl Kimi, GLM  | yes        | Free w/ NVIDIA dev acct                     |
| 7 | Groq               | Llama, Kimi, Qwen          | yes        | Generous RPM/TPD, fast                      |
| 8 | OpenRouter         | aggregator, `:free` models | yes        | Adds extra quota on top of direct           |
| 9 | Hugging Face       | Inference Providers        | yes        | Routed to Cerebras/Together/Fireworks/etc.  |

Lower priority / skip for now: Mistral La Plateforme (limited free), Anthropic (paid-only API), OpenAI (no sustained free API), Perplexity (paid).

## Phases

### Phase 0: Setup

- Create accounts for each provider. Track signup status in `providers/<name>.md`.
- Store API keys in `scripts/.env` (gitignored). Never commit.
- Mark provider "skipped" if signup blocked (e.g., requires CC, region locked, KYC).

### Phase 1: Connectivity probe

For each provider, send minimal chat-completion request:

```
POST {base_url}/chat/completions
{ "model": "<test_model>", "messages": [{"role":"user","content":"reply with: pong"}], "max_tokens": 5 }
```

Record:
- HTTP status, latency, response text.
- All response headers (esp. `x-ratelimit-*`, `retry-after`, `anthropic-*`, vendor-specific).
- Any quota-info endpoint exposed (`/v1/usage`, `/dashboard/billing/credit_grants`, etc.).

### Phase 2: Quota visibility

Goal: figure out how to know remaining quota WITHOUT depleting it.

For each provider classify quota-tracking strategy:
- **Header-exposed**: response carries remaining RPM/RPD/TPM. Read on each call.
- **Endpoint-exposed**: separate GET for remaining credit/quota.
- **Opaque**: must track locally (token-bucket from known limits) and treat 429 as authoritative.

Document:
- Exact header names + units.
- Reset semantics (per-minute window vs sliding).
- 429 behavior + `Retry-After` value.

### Phase 3: Capability sniff (optional, skip if quota expensive)

One small reasoning prompt + one code prompt per model. Verdict: keep / drop.

### Phase 4: Findings

Fill `findings.md` summary table:

| Provider | Status | Models kept | RPM | RPD | TPM | TPD | Quota visibility | Tier | Notes |

Where Tier = T1 (high reasoning), T2 (general), T3 (fast/cheap).

### Phase 5: Bank design (separate file)

After Phase 4, write `bank-design.md`:
- Provider registry schema (provider, model, tier, limits, key, quota source).
- Token-bucket per (provider, model). Refill from spec, hard reset on 429.
- Selection policy: tier filter -> liveness filter -> highest remaining quota -> fastest.
- Failover: on 429/5xx, mark cooldown until `Retry-After` or N seconds, retry next.
- Persistence: SQLite file or JSON snapshot for state across restarts (Redis overkill for single host).

## Per-provider note template

```markdown
# <Provider>

- **Status**: untested | working | broken | skipped
- **Signup**: <url>, requires CC? phone? region?
- **Auth**: API key header `Authorization: Bearer ...` | `x-api-key: ...`
- **Base URL**: https://...
- **OpenAI-compatible path**: /v1/chat/completions or native
- **Models tested**: model_id -> verdict
- **Rate limits (documented)**: RPM, RPD, TPM, TPD, monthly tokens
- **Rate limits (observed)**: from headers / from 429
- **Quota visibility**: header-exposed | endpoint | opaque
  - Header names: ...
  - Endpoint: ...
- **Failure modes**: 429 body, 503 frequency, retry-after honored?
- **Notes**: anything sharp (region lock, model deprecation, weird default temp).
```

## Risks + open questions

- Many providers reset quotas at midnight UTC vs rolling window. Confirm per provider.
- Some "free" tiers train on your data (Google AI Studio default-on). Add `data_used_for_training: bool` field to registry.
- Retry-After missing on some providers; need conservative default cooldown.
- OAI-compat endpoints sometimes drop tool/function calling fields. Test if needed.
- HuggingFace Inference Providers route to other backends, double-counting limits possible.

## Next action

Create `providers/` stubs from template. Then create accounts in priority order: Cerebras, Groq, DeepSeek, Google AI Studio, NVIDIA NIM, SambaNova, Moonshot, OpenRouter, HuggingFace.
