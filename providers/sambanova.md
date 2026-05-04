# SambaNova Cloud

- **Status**: working (6/6 tested)
- **Signup**: https://cloud.sambanova.ai/ — email + phone, no CC.
- **Auth**: `Authorization: Bearer <key>`.
- **Base URL**: `https://api.sambanova.ai/v1/` (OAI-compatible).
- **Models tested (2026-05-03)** — all 200 OK:
  - `Meta-Llama-3.3-70B-Instruct` (T2) → 1127 ms.
  - `DeepSeek-V3.2` (T1) → 891 ms.
  - `DeepSeek-V3.1` (T1) → 507 ms.
  - `Llama-4-Maverick-17B-128E-Instruct` (T2) → 2258 ms.
  - `gpt-oss-120b` (T1) → 402 ms.
  - `gemma-3-12b-it` (T3) → 1133 ms.
- **Deprecated (drop)**: `DeepSeek-R1` → 410 Gone (use V3.2).
- **Rate limits (observed, free)**: **20 RPD per model only**. No RPM/TPM headers exposed.
- **Quota visibility**: headers — `x-ratelimit-limit-requests-day`, `x-ratelimit-remaining-requests-day`, `x-ratelimit-reset-requests-day` (epoch).
- **Failure modes**: 410 for deprecated models; 429 RPD when daily cap hit.
- **Privacy**: standard cloud TOS. `data_used_for_training: false`.
- **Notes**: 20 RPD is tight. Use sparingly — bank should select SambaNova **last** unless model is unique to it (DeepSeek-V3.2, Llama-4-Maverick). Latency 0.4–2.3 s is fine.
- **Bank decision**: header-trusted (RPD only). Treat RPM/TPM as unknown (no soft limit, just rely on 429). Priority below Groq/HF/Cerebras.
