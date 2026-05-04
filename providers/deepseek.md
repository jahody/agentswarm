# DeepSeek

- **Status**: **DROP** — paid-only as of 2026-05-03
- **Probe results (2026-05-03)**:
  - `deepseek-chat`, `deepseek-reasoner` → 402 "Insufficient Balance" on first call.
  - `GET /user/balance` confirmed: `granted_balance: "0.00"`, `topped_up_balance: "0.00"`, `is_available: false`.
  - 5 M signup-credit promo no longer granted to new accounts.
- **Re-evaluate**: only if user funds the account.
- **Signup**: https://platform.deepseek.com/ — phone or email. Source doc claims 5M free tokens for new accounts (verify).
- **Auth**: `Authorization: Bearer <key>`.
- **Base URL**: `https://api.deepseek.com/` (OAI-compatible at `/chat/completions`).
- **Models to test**: `deepseek-chat` (V3/V4), `deepseek-reasoner` (R1).
- **Rate limits**: not strictly documented; soft RPM. Server-busy 503 common at peak.
- **Quota visibility**: balance endpoint `GET /user/balance` returns remaining credit. Use for "remaining quota" signal.
- **Failure modes**: 503 frequent, 429 rare. Streaming sometimes truncates.
- **Privacy**: data may be retained; opt-out only on enterprise. Flag.
- **Notes**: confirm whether 5M free credit is current promo or expired. If gone, treat as paid-only.
