# Hugging Face Inference Providers

- **Status**: working (7/7 tested)
- **Signup**: https://huggingface.co/ — free account; token at https://huggingface.co/settings/tokens.
- **Auth**: `Authorization: Bearer <hf_token>`.
- **Base URL**: `https://router.huggingface.co/v1/` (OAI-compatible router across providers).
- **Models tested (2026-05-03)** — all 200 OK:
  - `deepseek-ai/DeepSeek-R1` (T1) → 704 ms.
  - `deepseek-ai/DeepSeek-V3.2` (T1) → 1446 ms.
  - `meta-llama/Llama-3.3-70B-Instruct` (T2) → 336 ms.
  - `Qwen/Qwen3-235B-A22B-Instruct-2507` (T2) → 532 ms.
  - `Qwen/Qwen3-Next-80B-A3B-Instruct` (T2) → 755 ms.
  - `Qwen/Qwen3-Coder-480B-A35B-Instruct` (T2 code) → 416 ms.
  - `google/gemma-3-27b-it` (T3) → 614 ms.
- **Rate limits (observed)**: 600 RPM, 1 M TPM (per `x-ratelimit-limit-*`); 300 remaining requests after 1 call → likely sliding 1-min window. Generous.
- **Quota visibility**: response headers — `x-ratelimit-{limit,remaining,reset}-{requests,tokens}`. Reset values in ms. **Headers reflect router state, not the underlying backend.**
- **Failure modes**: 402 when monthly credit gone (not observed); 429 upstream.
- **Privacy**: depends on backend route (Cerebras/Together/Fireworks/Hyperbolic). Conservatively `data_used_for_training: false` for top-tier backends.
- **Notes**: routes across providers — same model may run on different backends across calls. Append `:provider` to pin (e.g. `deepseek-ai/DeepSeek-R1:fireworks-ai`). **Risk**: backend may be a provider we already have a direct key for, causing double-counted quota. Treat as separate provider in bank state.
- **Bank decision**: header-trusted. **Primary T1 target** alongside Cerebras/Groq for T2/T3.
