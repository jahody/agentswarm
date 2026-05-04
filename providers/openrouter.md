# OpenRouter (free models)

- **Status**: working partial — 6/12 tested `:free` slugs returned 200; 5 returned 429 upstream rate-limited (Venice provider); 1 returned 402 (Venice key spend cap)
- **Probe results (2026-05-03)** — 200 OK:
  - `openai/gpt-oss-120b:free` (T1) → 1840 ms
  - `nvidia/nemotron-3-super-120b-a12b:free` (T1) → 534 ms
  - `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` (T1) → 510 ms
  - `z-ai/glm-4.5-air:free` (T1) → 10.8 s
  - `openai/gpt-oss-20b:free` (T2) → 13.4 s
  - `google/gemma-3-12b-it:free` (T3) → 2.7 s
- **Probe results — 429/402** (re-try later):
  - `nousresearch/hermes-3-llama-3.1-405b:free`, `qwen/qwen3-next-80b-a3b-instruct:free`, `qwen/qwen3-coder:free`, `minimax/minimax-m2.5:free`, `meta-llama/llama-3.2-3b-instruct:free` → 429 (Venice upstream rate-limited).
  - `meta-llama/llama-3.3-70b-instruct:free` → 402 (Venice key USD spend cap exceeded).
- **Stale slugs (drop)**: `deepseek/deepseek-r1*:free`, `qwen/qwen-2.5-72b-instruct:free`, `mistralai/mistral-small-3.1-24b-instruct:free`, `google/gemini-2.5-pro-exp:free` — not in live `/v1/models` `:free` filter.
- **Account state (`/auth/key`)**: `is_free_tier: false`, `limit: 10` USD, `limit_remaining: 10`, `usage: 0`, `is_management_key: false`.
- **Signup**: https://openrouter.ai/ — Google/GitHub, no CC for free tier.
- **Auth**: `Authorization: Bearer <key>`. Optional `HTTP-Referer` + `X-Title` for analytics + per-app rate-limit isolation.
- **Base URL**: `https://openrouter.ai/api/v1/` (OAI-compatible).

## Free model catalog (probe these)

OpenRouter's free models share one global free-tier bucket per account. Pick representatives across tiers; do NOT register every `:free` model — they all draw from same RPM cap.

| Tier | Model id                                          | Why                              |
|------|---------------------------------------------------|----------------------------------|
| T1   | `deepseek/deepseek-r1:free`                       | Strong reasoning, free           |
| T1   | `deepseek/deepseek-r1-0528:free`                  | Newer R1 snapshot                |
| T1   | `google/gemini-2.5-pro-exp:free`                  | If still routed (drops in/out)   |
| T2   | `meta-llama/llama-3.3-70b-instruct:free`          | General                          |
| T2   | `qwen/qwen-2.5-72b-instruct:free`                 | General, multilingual            |
| T2   | `qwen/qwen3-coder:free`                           | Code                             |
| T2   | `mistralai/mistral-small-3.1-24b-instruct:free`   | Lightweight general              |
| T3   | `meta-llama/llama-3.2-3b-instruct:free`           | Fast/cheap                       |
| T3   | `google/gemma-3-27b-it:free`                      | Fast/cheap                       |

Live list discoverable: `GET https://openrouter.ai/api/v1/models` then filter `id` ending `:free`. Add to `findings.md` after listing live.

## Rate limits (documented, free tier)

- **20 RPM** combined across all `:free` models.
- **50 RPD** when lifetime account credit < $10.
- **1000 RPD** when lifetime credit ≥ $10 (one-time $10 top-up unlocks; credit not consumed by `:free` calls).
- Per-model "training/usage" limits sometimes apply on top (rare).

## Quota visibility

- **Response headers** (per call): `x-ratelimit-limit`, `x-ratelimit-remaining`, `x-ratelimit-reset` (epoch ms). Trust as authoritative.
- **Endpoint**: `GET /api/v1/auth/key` → `{ data: { label, limit, usage, is_free_tier, rate_limit: { requests, interval } } }`. Use to seed counters at startup.
- **Endpoint**: `GET /api/v1/credits` → purchased credit balance.

## Failure modes

- 429 w/ `retry-after` (seconds). Honor it.
- Upstream 5xx surfaced as 502/503 with `{error: {message, code, metadata: {provider_name}}}`. Treat as transient, retry next eligible.
- Some `:free` slots silently route to a paid backup when the free pool is empty; OpenRouter rejects that on `:free` suffix, so you'll see 402 instead. Map 402 → mark account `disabled` for `:free` until next reset.

## Bank treatment

- Register OpenRouter as **single logical provider** with one shared bucket (`rpm=20, rpd=50_or_1000`).
- Multiple model_ids share state — bank tracks the shared bucket at provider-level for OpenRouter, not per-model.
- Priority: **below** direct providers (Groq, Cerebras, DeepSeek). Use as breadth fallback when direct-provider models exhausted or model not directly available.
- Set `data_used_for_training: true` for `:free` models — OpenRouter requires opt-in to data sharing for free routing.

## Notes

- $10 top-up is the cheapest way to lift RPD from 50 → 1000 without paying per call. Worth flagging to user as optional cost-effective unlock.
- Free routing pool changes weekly — re-probe `/v1/models` monthly and prune dead `:free` ids.
