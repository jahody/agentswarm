# Agent Bank: Design Sketch

NOTE: skeleton + Phase-1 results folded in (2026-05-03). Concrete model lists in `scripts/providers.yaml`. Phase-1 verdicts in `findings.md`.

## Components

```
+--------------------+
|   bank.acquire(    |   pick best (provider, model) for tier+budget
|     tier=T1,       |
|     min_tokens=...)|
+---------+----------+
          |
          v
+-------------------+      +--------------------+
|   Registry (yaml) |<-----| providers.yaml     |
|   + live state    |      | findings.md        |
+---------+---------+      +--------------------+
          |
          v
+-------------------+
| QuotaTracker      |  per (provider, model): bucket(rpm,tpm) + counters(rpd,tpd) + cooldown
+---------+---------+
          |
          v
+-------------------+
| HTTP client       |  OAI-compat call; parses headers; updates tracker; handles 429/5xx
+-------------------+
```

## Data model

```python
@dataclass
class Provider:
    name: str
    base_url: str
    key_env: str
    quota_endpoint: str | None
    headers_expose_remaining: bool
    data_used_for_training: bool

@dataclass
class Model:
    provider: str
    model_id: str
    tier: Literal["T1","T2","T3"]
    rpm: int | None
    rpd: int | None
    tpm: int | None
    tpd: int | None
    monthly_tokens: int | None
    context_window: int

@dataclass
class LiveState:
    rpm_remaining: int | None
    rpd_remaining: int | None
    tpm_remaining: int | None
    tpd_remaining: int | None
    cooldown_until: float | None  # epoch seconds
    last_429_at: float | None
    last_success_at: float | None
```

## Selection policy (acquire)

1. Filter by `tier`.
2. Drop entries with `cooldown_until > now`.
3. Drop entries where `min_tokens > tpm_remaining` (when known).
4. Sort by `(rpm_remaining desc, rpd_remaining desc, latency_avg asc)`.
5. Return top-1 with a lease (decrement local counters optimistically).
6. Caller calls `bank.report(lease, response)` with HTTP status + headers; bank reconciles.

## Quota tracking strategy

- **Header-exposed providers** (Groq, Cerebras, OpenRouter): trust headers as authoritative; overwrite local counters on every response.
- **Endpoint-exposed providers** (DeepSeek balance, OpenRouter `/auth/key`): poll endpoint at most once/N seconds, cache.
- **Opaque providers** (Google AI Studio, NVIDIA NIM, HuggingFace): local token-bucket from documented limits. On 429, set `cooldown_until = now + max(retry_after, 60s)` and zero `rpm_remaining`.

### Shared-bucket providers (OpenRouter)

OpenRouter's `:free` models share a single account-wide RPM/RPD bucket. Bank stores `LiveState` at **provider-level** (not per-model) when `shared_bucket: true` in `providers.yaml`. Selection still picks a model_id (for capability), but quota check + decrement target the provider bucket. Avoids over-counting and prevents the bank from thinking it has 9× the real capacity.

## Failover

- 429 → mark cooldown, retry next eligible model in same tier.
- 5xx → exponential backoff per (provider, model); after K failures shift to next provider.
- 401/403 → mark provider `disabled=true`, surface to user (key revoked).

## Persistence

Single-host scope. SQLite file `bank-state.sqlite` with tables:
- `providers(name, disabled, last_check_at)`
- `models(provider, model_id, tier, rpm, rpd, tpm, tpd, monthly_tokens)`
- `live(provider, model_id, rpm_remaining, rpd_remaining, tpm_remaining, tpd_remaining, cooldown_until, updated_at)`
- `events(ts, provider, model_id, status, latency_ms, error)`
- `disabled_models(provider, model_id, reason, since_ts)`  # permanent disables (404/410/gated)

Reset `rpd_remaining` daily based on documented reset hour (UTC midnight assumed).

## Surface API

```python
bank = Bank.from_yaml("providers.yaml", state="bank-state.sqlite")
resp = bank.chat(
    messages=[...],
    tier="T2",
    max_tokens=1024,
    prefer=["groq", "cerebras"],   # optional
    avoid=["google_ai_studio"],    # optional
)
# resp = OAI-shaped response + .meta with provider, model, latency, remaining
```

## Decisions (resolved 2026-05-03)

- **T1 selection**: **quality first, then quota**. Order: HuggingFace (DeepSeek-R1/V3.2, Qwen3-235B) → NVIDIA NIM (kimi-k2-instruct, gpt-oss-120b, nemotron-super-49b) → SambaNova (DeepSeek-V3.2; sparing, 20 RPD) → OpenRouter `:free` (fallback). Cerebras `qwen-3-235b` retried opportunistically.
- **T2 selection**: **quota-first** since latency tight on Groq. Order: Groq → HuggingFace → SambaNova → Google AI Studio (flash) → NVIDIA NIM → OpenRouter `:free`.
- **T3 selection**: **latency-first**. Order: Groq llama-3.1-8b-instant → Cerebras llama3.1-8b → Google AI Studio flash-lite → SambaNova gemma-3-12b-it → HuggingFace gemma-3-27b-it → OpenRouter `:free`.
- **Sliding-window vs fixed-day reset**:
  - Cerebras: per-(minute, hour, day) windows, all rolling.
  - Groq: per-window inferred from `reset-requests` value (`6s` ≈ sub-minute, `1m26.4s` ≈ per-minute). Treat as rolling.
  - SambaNova: RPD with epoch reset header, treat as rolling 24 h.
  - HuggingFace: ms-resolution rolling.
  - OpenRouter: `/auth/key.limit_reset = null` → treat as monthly USD spend cap; per-call `:free` bucket = 20 RPM / 50 RPD account-wide, sliding.
  - Google AI Studio + NVIDIA NIM: opaque; assume daily UTC midnight reset for local bucket.
- **Output-token estimation for TPM accounting**: **start input-only**. Revise once we observe TPM-on-output 429s. Bank uses `tpm_remaining` only as soft hint; falls back on 429.
- **Concurrency**: **single process**. SQLite WAL. Add multi-process later if a swarm runs.
- **Per-provider client timeout**: 60 s default; **120 s for NVIDIA NIM** (cold-start observed).
- **Retry policy**: 1 retry on ReadTimeout / 5xx with 5 s backoff before failover. 429 → no retry; failover immediately, mark cooldown.
- **Cooldown defaults**:
  - 429 with `Retry-After` → exact value.
  - 429 without `Retry-After` (Cerebras `queue_exceeded`, OpenRouter upstream) → 60 s.
  - 402 → 24 h (likely paid-only or spend-cap).
  - 410 / 404 model_not_found → permanent disable for that model_id; surface to user.
  - ReadTimeout → 30 s cooldown.
- **Provider drops (paid-only)**: DeepSeek + Moonshot. Not registered in bank.
- **Persistence schema**: keep as designed. Add `disabled_models(provider, model_id, reason, since_ts)` table for permanent-disable model_ids (404, 410, gated).
