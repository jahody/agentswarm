# Groq

- **Status**: working (6/6 tested)
- **Signup**: https://console.groq.com/ — Google/GitHub login, no CC.
- **Auth**: `Authorization: Bearer <key>`.
- **Base URL**: `https://api.groq.com/openai/v1/` (OAI-compatible).
- **Models tested (2026-05-03)** — all 200 OK:
  - `llama-3.3-70b-versatile` (T2) → 295 ms; 1 k req-window, 12 k tok-window, reset 1m26.4s.
  - `llama-3.1-8b-instant` (T3) → 62 ms; 14.4 k req-window, 6 k tok-window, reset 6 s.
  - `qwen/qwen3-32b` (T2) → 1 k req, 6 k tok, reset 1m26.4s.
  - `meta-llama/llama-4-scout-17b-16e-instruct` (T2) → 1 k req, 30 k tok.
  - `openai/gpt-oss-120b` (T1) → 1 k req, 8 k tok.
  - `openai/gpt-oss-20b` (T2/T3) → 1 k req, 8 k tok.
- **Decommissioned (drop)**: `qwen-qwq-32b`, `deepseek-r1-distill-llama-70b`.
- **Not hosted (drop)**: `moonshotai/kimi-k2-instruct` — 404.
- **Rate limits (observed, per model)**: see above. RPM/TPM differ per model.
- **Quota visibility**: response headers `x-ratelimit-{limit,remaining}-{requests,tokens}` + `x-ratelimit-reset-{requests,tokens}`. Window inferred from reset interval (6 s = sub-minute, 1m26.4s ≈ per-minute).
- **Failure modes**: 400 `model_decommissioned` for stale IDs; 404 for not-hosted; 429 expected to honor `retry-after`.
- **Speed**: LPU sub-300 ms typical. **Primary T2/T3 target**.
- **Bank decision**: header-trusted. Per-model state. Default for T2/T3 tier.
