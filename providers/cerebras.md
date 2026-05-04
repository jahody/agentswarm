# Cerebras Cloud

- **Status**: working (1 of 4 tested model IDs; 2 gated, 1 transient queue 429)
- **Signup**: https://cloud.cerebras.ai/ — Google/email signup, no CC.
- **Auth**: `Authorization: Bearer <key>`.
- **Base URL**: `https://api.cerebras.ai/v1/` (OAI-compatible).
- **Models tested (2026-05-03)**:
  - `llama3.1-8b` → 200 OK, ~4–6 s latency. **Keep (T3)**.
  - `qwen-3-235b-a22b-instruct-2507` → 429 `queue_exceeded` once; listed in `/v1/models`. Keep, retry-friendly.
  - `gpt-oss-120b` → 404 "do not have access". Gated. **Drop**.
  - `zai-glm-4.7` → 404 "do not have access". Gated. **Drop**.
- **Rate limits (observed, free)**: 30 RPM, 900 RPH, 14,400 RPD; 60 k TPM, 1 M TPH, 1 M TPD (per model).
- **Quota visibility**: response headers — `x-ratelimit-{limit,remaining}-{requests,tokens}-{minute,hour,day}`. **Richest of any provider tested.**
- **Failure modes**:
  - 429 `queue_exceeded` (transient queue, no `retry-after`) — wait 60 s, retry next eligible.
  - 404 model_not_found — model exists in catalog but ungranted on free tier.
- **Speed**: 4–6 s on first request (cold start), faster subsequently. Treat T3.
- **Notes**: prefer for T3 fast tier despite cold-start latency. Quota tracking is the cheapest of the kept providers.
- **Bank decision**: header-trusted. Refresh local state from headers on every response.
