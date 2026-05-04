# Moonshot AI (Kimi)

- **Status**: **DROP** — account auto-suspended at $0 balance as of 2026-05-03
- **Probe results (2026-05-03)**:
  - `moonshot-v1-8k`, `moonshot-v1-128k` → 429 "account suspended due to insufficient balance".
  - `GET /v1/users/me/balance` confirmed: `available_balance: 0`, `voucher_balance: 0`, `cash_balance: 0`.
  - $5 voucher promo no longer granted.
- **Alternate route**: NVIDIA NIM hosts `moonshotai/kimi-k2-instruct` and `kimi-k2-thinking` free. Use NIM instead.
- **Re-evaluate Moonshot direct**: only if user funds the account.
- **Signup**: https://platform.moonshot.ai/ — phone (CN) or international portal. May require KYC.
- **Auth**: `Authorization: Bearer <key>`.
- **Base URL**: `https://api.moonshot.ai/v1/` (international) or `https://api.moonshot.cn/v1/` (CN).
- **Models to test**: `moonshot-v1-8k`, `moonshot-v1-32k`, `moonshot-v1-128k`, `kimi-k2-*`.
- **Rate limits**: $5 voucher = ~credit; soft RPM cap. Server-busy 503 common.
- **Quota visibility**: balance endpoint exists; check `GET /v1/users/me/balance` (verify path).
- **Alt access**: NVIDIA NIM hosts Kimi K2.5 free with OAI-compat — see `nvidia-nim.md`.
- **Privacy**: review TOS; assume training-on-data.
- **Notes**: signup outside CN may be limited. If blocked, use NIM route.
