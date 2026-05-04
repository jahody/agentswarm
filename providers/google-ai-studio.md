# Google AI Studio (Gemini)

- **Status**: working (2/3 tested)
- **Signup**: https://aistudio.google.com/ — Google account, no CC.
- **Auth**: `Authorization: Bearer <key>` (OAI-compat used here).
- **Base URL**: `https://generativelanguage.googleapis.com/v1beta/openai/`
- **Models tested (2026-05-03)**:
  - `gemini-2.5-flash` (T2) → 200, 614 ms.
  - `gemini-2.5-flash-lite` (T3) → 200, 1117–1353 ms.
  - `gemini-2.5-pro` → **429 hard** (`limit: 0` on free tier). **Drop from free catalog**.
- **Rate limits (free tier, documented):**
  - 2.5 Flash: 10 RPM / 250 RPD (subject to change).
  - 2.5 Flash-Lite: 30 RPM / 14k RPD.
  - 2.5 Pro: paid only.
- **Quota visibility**: **opaque** — no `x-ratelimit-*` headers on either OAI-compat or native path. Bank tracks locally from documented limits; 429 body is authoritative.
- **Failure modes**: 429 carries quota detail in error body (`generate_content_free_tier_requests`, `limit: 0`). Native API includes `retryDelay`; OAI-compat does not.
- **Privacy**: **free tier inputs used for training by default**. `data_used_for_training: true`.
- **Notes**: OAI-compat path used; loses Gemini-specific features (thinking budget). Acceptable for this bank.
- **Bank decision**: opaque tracking. Local token-bucket from documented RPM/RPD. Hard 429 → 60 s cooldown then retry next provider.
