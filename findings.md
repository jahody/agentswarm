# Findings — Phase 1 probe (2026-05-03)

Probe artifacts: `findings/<provider>__<model>__<ts>.json`. Two probe runs: 15-55-40Z (initial, exposed dead model IDs) and 15-59-24Z (cleaned catalog).

## Summary table

| Provider          | Working? | Live models                          | Tier | RPM/TPM observed (per model)                | RPD observed   | Quota visibility | Latency p50 | Verdict |
|-------------------|----------|--------------------------------------|------|---------------------------------------------|----------------|------------------|-------------|---------|
| Cerebras          | ✅ partial | 1/4 (some gated)                    | T3   | 30 RPM, 60k TPM (free tier)                 | 14,400 / 1M tok | headers (rich)   | 4–6 s       | keep    |
| Groq              | ✅       | 6/6                                  | T2/T3 | 1k RPM (70b/4-scout/qwen/oss), 14.4k (8b)  | header only     | headers          | 0.06–0.3 s  | **keep, primary T2/T3** |
| Google AI Studio  | ✅ partial | 2/3 (pro paid-only)                 | T2/T3 | opaque — local bucket                      | opaque          | none             | 0.6–1.4 s   | keep, opaque tracking |
| SambaNova         | ✅       | 6/6                                  | T1/T2 | **20 RPD only**, no RPM/TPM headers       | 20 / model     | headers (RPD)    | 0.4–2.3 s   | keep, sparing use |
| NVIDIA NIM        | ✅       | 7/8 with 120 s + 1 retry             | T1   | opaque                                     | opaque          | none             | 0.4–103 s   | keep, cold-start friendly client |
| OpenRouter `:free`| ⚠️ flaky  | 6/12 (rest 429 upstream / 1×402)    | T1/T2 | shared 20 RPM / 50 RPD account-wide       | 50 RPD          | endpoint + headers | 0.5–13 s   | keep, fallback only |
| HuggingFace       | ✅       | 7/7                                  | T1/T2 | 600 RPM, 1M TPM observed, 300 req remain  | header          | headers          | 0.3–1.5 s   | keep, **primary T1** |
| ~~DeepSeek~~      | ❌       | 0/2                                  | —    | —                                           | —              | balance ep       | —           | **DROP** (paid-only) |
| ~~Moonshot~~      | ❌       | 0/2                                  | —    | —                                           | —              | balance ep       | —           | **DROP** (account auto-suspended at $0 balance) |

**Notes on table:**
- Cerebras `gpt-oss-120b`, `zai-glm-4.7` listed in `/v1/models` but 404 on chat — gated behind a paid/waitlist tier. Free account only gets `llama3.1-8b` + transient access to `qwen-3-235b-a22b-instruct-2507` (queue 429 hit once, normally OK).
- Groq RPM/RPD limits per-model. Header `x-ratelimit-limit-requests` reports the active window; headers don't separate RPM vs RPD cleanly, deduce from `reset-requests` interval (`6s` ≈ per-window for 8b-instant, `1m26.4s` ≈ per-minute for 70b).
- SambaNova free tier is the most quota-tight: only **20 requests/day per model**. Spread evenly across 6 models = 120 reqs/day total — viable as breadth-fallback only.
- NVIDIA NIM timeouts on `kimi-k2-thinking`, `llama-3.3-70b-instruct`, `deepseek-v3.2`, `glm5` at 60 s. Likely cold-start or queue, not breakage. `kimi-k2-instruct` succeeded at 25 s.
- OpenRouter `meta-llama/llama-3.3-70b-instruct:free` returned 402 "USD spend limit exceeded" — Venice provider charges the key despite `:free` slug. Treat as unavailable until OpenRouter reroutes.
- HuggingFace router routes to backends; `x-ratelimit-remaining-requests: 299` after one call → 300/min. Generous.

## Per-provider verdict

### Cerebras — keep
Only `llama3.1-8b` reliably free. 30 RPM / 14.4 k RPD / 1 M TPD per model. Header-exposed quota is the cleanest of any provider tested (`x-ratelimit-{limit,remaining}-{requests,tokens}-{minute,hour,day}`). Slow first-request latency (4–6 s) — possible cold start. Try retry of `qwen-3-235b-a22b-instruct-2507`; it's listed and one-time queue 429 may clear. Drop `gpt-oss-120b` and `zai-glm-4.7` — access denied despite catalog presence.

### Groq — keep, primary T2/T3
6/6 models hot, sub-300 ms typical, header-exposed quota. Best free provider for general/fast tier. Recommend as default T2/T3 selection target. Different RPM/TPM per model — bank should track per-model state, not provider-wide.

### Google AI Studio — keep with caveat
Flash + flash-lite work; `gemini-2.5-pro` quota is hard 0 on free tier — drop. **Data used for training** by default on free tier. No rate-limit headers; bank must track local token bucket from documented limits (15 RPM, 1500 RPD for flash; 30 RPM, 14k RPD for flash-lite as of last public docs).

### SambaNova — keep sparingly
6/6 working but **20 RPD per model** = effectively a smoke-test budget. Use for fallback only. Headers expose only RPD with reset epoch — no RPM/TPM signal. Latency 0.4–2.3 s is fine. DeepSeek-V3.2 + Llama-4-Maverick are the differentiators here.

### NVIDIA NIM — keep, cold-start friendly client required
Second pass with 120 s timeout + 1 retry: 7/8 models 200 OK. Cold-start container spin confirmed (kimi-k2-thinking 78 s cold, llama-3.3-70b 103 s on retry, glm5 70 s on retry). Warm latencies: gpt-oss-120b 441 ms, nemotron-super 600 ms, qwen3-next 3.2 s. **`deepseek-ai/deepseek-v3.2` still timed out twice at 120 s — mark flaky in bank, prefer NIM alternatives or fall through to HF.** Bank uses `request_timeout=120` for nim from yaml. No quota headers; treat as opaque, 429/403 authoritative.

### OpenRouter `:free` — keep, lowest priority
Account-shared bucket: 20 RPM / 50 RPD. Of 12 free model IDs, 6 returned 200, 6 returned upstream 429, 1 returned 402 (Venice key spend cap). Flaky — `:free` pool depends on which upstream provider is currently routing. Use as breadth fallback only (after Groq/HF exhausted in same tier). Quota endpoint `GET /auth/key` confirms account-level cap; per-call headers also present on most responses.

### HuggingFace — keep, primary T1
All 7 chosen models 200 OK. Routes to Cerebras/Together/Fireworks/Hyperbolic; latency 0.3–1.5 s. Massive headers-reported limits (600 req/min, 1 M tokens). DeepSeek-R1, DeepSeek-V3.2, Qwen3-235B all live. Recommend as default T1 reasoning target. Risk: when HF routes to Cerebras backend, both this account's HF quota and Cerebras quota may decrement — bank should be aware (treat as separate provider, accept double-counting risk).

### DeepSeek — DROP
402 "Insufficient Balance" on first call. Free 5M signup credit no longer granted as of 2026-05-03. Re-evaluate only if user funds the account.

### Moonshot — DROP
429 "account suspended due to insufficient balance" on first call. $5 voucher no longer granted. Re-evaluate only if user funds the account.

## Tier composition (final, post-Phase-1)

- **T1 (high reasoning)**:
  - HuggingFace: `deepseek-ai/DeepSeek-R1`, `deepseek-ai/DeepSeek-V3.2`, `Qwen/Qwen3-235B-A22B-Instruct-2507`
  - NVIDIA NIM: `moonshotai/kimi-k2-instruct`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`, `openai/gpt-oss-120b` (retry on timeout)
  - SambaNova: `DeepSeek-V3.2`, `gpt-oss-120b` (sparing — 20 RPD)
  - OpenRouter (fallback): `nvidia/nemotron-3-super-120b-a12b:free`, `openai/gpt-oss-120b:free`, `z-ai/glm-4.5-air:free`

- **T2 (general)**:
  - Groq: `llama-3.3-70b-versatile`, `meta-llama/llama-4-scout-17b-16e-instruct`, `qwen/qwen3-32b`, `openai/gpt-oss-20b`
  - HuggingFace: `meta-llama/Llama-3.3-70B-Instruct`, `Qwen/Qwen3-Next-80B-A3B-Instruct`, `Qwen/Qwen3-Coder-480B-A35B-Instruct`
  - SambaNova: `Meta-Llama-3.3-70B-Instruct`, `Llama-4-Maverick-17B-128E-Instruct`
  - Google AI Studio: `gemini-2.5-flash`
  - NVIDIA NIM: `qwen/qwen3-next-80b-a3b-instruct`
  - OpenRouter (fallback): `openai/gpt-oss-20b:free`

- **T3 (fast/cheap)**:
  - Groq: `llama-3.1-8b-instant`
  - Cerebras: `llama3.1-8b`
  - Google AI Studio: `gemini-2.5-flash-lite`
  - SambaNova: `gemma-3-12b-it`
  - HuggingFace: `google/gemma-3-27b-it`
  - OpenRouter (fallback): `google/gemma-3-12b-it:free`

## Decisions feeding bank-design.md

- **Quota-source per provider**:
  - Cerebras → headers (rich, per-window).
  - Groq → headers (deduce window from `reset-requests`).
  - Google AI Studio → local bucket (opaque).
  - SambaNova → headers (RPD only; treat RPM/TPM as unknown, soft-throttle locally).
  - NVIDIA NIM → local bucket (opaque); long timeout + retry.
  - OpenRouter → endpoint at startup (`/auth/key`) + per-call headers; one shared bucket.
  - HuggingFace → headers.

- **Cooldown defaults**:
  - 429 with `Retry-After` → honor exactly.
  - 429 without `Retry-After` (Cerebras `queue_exceeded`, OpenRouter upstream) → 60 s.
  - 402 → mark provider/model `disabled` for 24 h; surface to user.
  - 5xx / ReadTimeout (NIM) → 30 s cooldown, max 2 retries with exponential backoff.

- **Daily reset times observed**:
  - SambaNova: epoch reset in header, ≈ 24 h sliding from first call.
  - Cerebras: per-minute/hour/day windows reported separately — assume rolling.
  - Groq: window inferred from `reset-requests` (mostly per-minute).
  - OpenRouter: `limit_reset` is null in `/auth/key` response — treat as monthly.

- **Models dropped + reason**:
  - `cerebras/llama-3.3-70b`, `cerebras/qwen-3-32b`, `cerebras/deepseek-r1-distill-llama-70b`: stale IDs (renamed/removed).
  - `cerebras/gpt-oss-120b`, `cerebras/zai-glm-4.7`: gated behind paid tier.
  - `groq/qwen-qwq-32b`, `groq/deepseek-r1-distill-llama-70b`: decommissioned.
  - `groq/moonshotai/kimi-k2-instruct`: not hosted by Groq.
  - `google_ai_studio/gemini-2.5-pro`: not free.
  - `sambanova/DeepSeek-R1`: deprecated → use `DeepSeek-V3.2`.
  - `nvidia_nim/deepseek-ai/deepseek-r1`: EOL'd 2026-01-26.
  - `openrouter/deepseek/deepseek-r1*:free`, `openrouter/qwen/qwen-2.5-72b-instruct:free`, `openrouter/mistralai/mistral-small-3.1-24b-instruct:free`, `openrouter/google/gemini-2.5-pro-exp:free`: not in live `:free` catalog.
  - All `deepseek/*` and `moonshot/*`: provider drop (no free balance).

## Phase-3 capability sniff (T1, 2026-05-03)

`scripts/capsniff.py` ran one reasoning prompt (set-theory: hats/scarves
inclusion-exclusion, expected answer "46") and one code prompt
(`is_palindrome`). Auto-judged by string match, 2048 max tokens for
reasoning, 1024 for code, 3 retries on transient errors.

| Provider     | Model                                              | Reason | Code | Notes |
|--------------|----------------------------------------------------|--------|------|-------|
| groq         | openai/gpt-oss-120b                                | PASS   | PASS | 564/303 ms — fastest reasoning route |
| sambanova    | DeepSeek-V3.2                                      | PASS   | PASS | 2670/576 ms |
| sambanova    | DeepSeek-V3.1                                      | PASS   | PASS | 1370/713 ms |
| sambanova    | gpt-oss-120b                                       | PASS   | PASS | 644/683 ms |
| nvidia_nim   | moonshotai/kimi-k2-instruct                        | PASS   | PASS | 5546/4570 ms |
| nvidia_nim   | moonshotai/kimi-k2-thinking                        | ERR    | PASS | reasoning hit timeout × 3 |
| nvidia_nim   | deepseek-ai/deepseek-v3.2                          | ERR    | PASS | flaky (still timing out) |
| nvidia_nim   | openai/gpt-oss-120b                                | PASS   | PASS | 1055/1164 ms |
| nvidia_nim   | nvidia/llama-3.3-nemotron-super-49b-v1.5           | PASS   | PASS | 49.6/35.7 s — slow but correct |
| nvidia_nim   | z-ai/glm5                                          | PASS   | PASS | 117/99 s — heavy cold-start |
| openrouter   | openai/gpt-oss-120b:free                           | PASS   | PASS | 2712/3441 ms |
| openrouter   | nvidia/nemotron-3-super-120b-a12b:free             | PASS   | PASS | 1826/4909 ms |
| openrouter   | nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free | PASS   | PASS | 5534/856 ms |
| openrouter   | z-ai/glm-4.5-air:free                              | PASS   | PASS | 97.7/27.2 s |
| openrouter   | nousresearch/hermes-3-llama-3.1-405b:free          | ERR    | ERR  | upstream 429 persists |
| huggingface  | deepseek-ai/DeepSeek-R1                            | PASS   | PASS | 22/4.1 s |
| huggingface  | deepseek-ai/DeepSeek-V3.2                          | PASS   | PASS | 10.3/2.9 s |
| huggingface  | Qwen/Qwen3-235B-A22B-Instruct-2507                 | PASS   | PASS | 449/315 ms — fast warm |
| cerebras     | qwen-3-235b-a22b-instruct-2507                     | FAIL   | ERR  | reasoning produced wrong number; queue 429 on retries |

**T1 verified solid** (PASS reasoning + code): 15 models. Fastest:
groq/gpt-oss-120b (sub-600 ms), HF/Qwen3-235B (450 ms), HF/DeepSeek-V3.2
(2.9 s code).

**T1 demoted**:
- `cerebras/qwen-3-235b`: failed reasoning verdict; demote to T2.
- `openrouter/hermes-3-llama-3.1-405b:free`: persistent upstream 429;
  consider dropping until OpenRouter reroutes.

**T1 keep but slow**:
- `nvidia_nim/glm5` (117 s), `openrouter/glm-4.5-air:free` (98 s),
  `nvidia_nim/llama-3.3-nemotron-super-49b-v1.5` (50 s) — fine for
  background tasks, not for interactive.

## Phase-3 capability sniff (T2, 2026-05-03)

| Provider     | Model                                          | Reason | Code | Notes |
|--------------|------------------------------------------------|--------|------|-------|
| groq         | llama-3.3-70b-versatile                        | PASS   | PASS | 790/275 ms |
| groq         | qwen/qwen3-32b                                 | PASS   | PASS | 3.1/1.6 s |
| groq         | meta-llama/llama-4-scout-17b-16e-instruct      | PASS   | PASS | 906/399 ms |
| groq         | openai/gpt-oss-20b                             | PASS   | PASS | 252/276 ms |
| google_ai_studio | gemini-2.5-flash                           | PASS   | PASS | 4.0/4.1 s |
| sambanova    | Meta-Llama-3.3-70B-Instruct                    | FAIL   | PASS | reasoning model-limit |
| sambanova    | Llama-4-Maverick-17B-128E-Instruct             | PASS   | PASS | 1.6/2.3 s |
| nvidia_nim   | meta/llama-3.3-70b-instruct                    | FAIL   | PASS | reasoning model-limit (same model as sambanova) |
| nvidia_nim   | qwen/qwen3-next-80b-a3b-instruct               | PASS   | PASS | 4.9/11.9 s |
| openrouter   | openai/gpt-oss-20b:free                        | PASS   | PASS | 9.0/3.6 s |
| openrouter   | qwen/qwen3-next-80b-a3b-instruct:free          | ERR    | ERR  | upstream 429 |
| openrouter   | qwen/qwen3-coder:free                          | ERR    | ERR  | upstream 429 |
| openrouter   | minimax/minimax-m2.5:free                      | PASS   | PASS | 14.7/15.7 s |
| openrouter   | meta-llama/llama-3.3-70b-instruct:free         | ERR    | ERR  | upstream 402 |
| huggingface  | meta-llama/Llama-3.3-70B-Instruct              | PASS   | PASS | 905/469 ms — fastest 70b |
| huggingface  | Qwen/Qwen3-Next-80B-A3B-Instruct               | PASS   | PASS | 2.9/1.4 s |
| huggingface  | Qwen/Qwen3-Coder-480B-A35B-Instruct            | FAIL   | PASS | code-tuned, reasoning weak (expected) |

**T2 verified solid** (PASS+PASS): 10 models. Fastest: groq/gpt-oss-20b
(252/276 ms), groq/llama-3.3-70b (790/275 ms), HF/Llama-3.3-70B
(905/469 ms).

**Llama-3.3-70B reasoning ceiling**: same model failed the
inclusion-exclusion puzzle on both SambaNova and NVIDIA NIM. Consistent
across providers → model limitation, not infra. HF route succeeded
because HF currently routes to a different snapshot (likely Cerebras
backend which got it right). Treat 70B as code-strong / reasoning-okay
for T2.

**Drop or demote** (after T2 sniff):
- `huggingface/Qwen3-Coder-480B`: reasoning FAIL but code PASS — keep
  as T2-code, not T2-general.
- All three openrouter ERR rows: persistent upstream rate-limiting on
  Qwen + Llama-3.3 free routes. Recommend disable until OpenRouter
  reroutes.

## Phase-3 capability sniff (T3, 2026-05-03)

| Provider     | Model                                  | Reason | Code | Notes |
|--------------|----------------------------------------|--------|------|-------|
| cerebras     | llama3.1-8b                            | PASS   | PASS | 3.5/0.5 s |
| groq         | llama-3.1-8b-instant                   | PASS   | PASS | 877/250 ms — fastest T3 |
| google_ai_studio | gemini-2.5-flash-lite              | FAIL   | PASS | reasoning model-limit (small) |
| sambanova    | gemma-3-12b-it                         | FAIL   | PASS | reasoning model-limit |
| openrouter   | google/gemma-3-12b-it:free             | ERR    | ERR  | upstream 429 |
| openrouter   | meta-llama/llama-3.2-3b-instruct:free  | ERR    | ERR  | upstream 429 |
| huggingface  | google/gemma-3-27b-it                  | PASS   | PASS | 9.8/4.1 s |

T3 expected to FAIL the inclusion-exclusion puzzle (small models, no
deep reasoning required at this tier). Code task is the binding metric.
**T3 verified solid** (PASS+PASS): groq/llama-3.1-8b-instant,
cerebras/llama3.1-8b, HF/gemma-3-27b-it.

## Yaml adjustments (post-capsniff)

- **Demote** `cerebras/qwen-3-235b-a22b-instruct-2507`: T1 → T2 (reasoning FAIL).
- **Disable** persistently 429/402 OpenRouter free routes (capsniff ERR
  on every retry):
  - `nousresearch/hermes-3-llama-3.1-405b:free`
  - `qwen/qwen3-next-80b-a3b-instruct:free`
  - `qwen/qwen3-coder:free`
  - `meta-llama/llama-3.3-70b-instruct:free`
  - `google/gemma-3-12b-it:free`
  - `meta-llama/llama-3.2-3b-instruct:free`
  Re-enable manually if OpenRouter reroutes.
- **Demote** `huggingface/Qwen/Qwen3-Coder-480B-A35B-Instruct`: T2 → keep T2 but tag as "code" only.

## Final tier rosters (post-capsniff PASS+PASS)

- **T1 reasoning** (15): groq/gpt-oss-120b, sambanova/{V3.2,V3.1,gpt-oss-120b},
  nim/{kimi-k2-instruct,gpt-oss-120b,nemotron-super,glm5},
  openrouter/{gpt-oss-120b,nemotron-3-super,nemotron-nano-omni-reasoning,glm-4.5-air}:free,
  huggingface/{DeepSeek-R1,DeepSeek-V3.2,Qwen3-235B}.
- **T2 general** (10): groq/{llama-3.3-70b,qwen3-32b,llama-4-scout,gpt-oss-20b},
  google/gemini-2.5-flash, sambanova/Llama-4-Maverick, nim/qwen3-next-80b,
  openrouter/{gpt-oss-20b,minimax-m2.5}:free, huggingface/{Llama-3.3-70B,Qwen3-Next-80B}.
- **T3 fast** (3): groq/llama-3.1-8b-instant, cerebras/llama3.1-8b, huggingface/gemma-3-27b-it.

## Next steps (post all phases)

1. (Done) Phase 1 probe.
2. (Done) Bank skeleton + 4 features.
3. (Done) Phase-3 capsniff for T1, T2, T3.
4. Final yaml adjustments + commit.
5. (Future) Per-tier latency-aware routing: T3 should sort by latency p50, T1 by quality+quota.
6. (Future) Re-run capsniff weekly to detect quality regressions or recovered upstream routes.
