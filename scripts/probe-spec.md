# Probe Spec

Scripts go in this folder. Two probes only at this stage. Both write JSON results to `../findings/<provider>-<timestamp>.json`.

## probe.py — connectivity + header inspection

Inputs: provider name (CLI arg). Reads key from `.env`.

For each (provider, model_id) pair from `providers.yaml`:
1. Send minimal request:
   ```
   POST {base_url}/chat/completions
   model: <model>
   messages: [{role: user, content: "ping"}]
   max_tokens: 5
   temperature: 0
   ```
2. Capture: status, latency_ms, response_text, ALL response headers, request_id (if returned).
3. Hit each provider's quota/balance endpoint if known (table below).
4. Write JSON record:
   ```json
   {
     "provider": "groq",
     "model": "llama-3.3-70b-versatile",
     "ts": "2026-05-03T14:00:00Z",
     "status": 200,
     "latency_ms": 312,
     "response_text": "pong",
     "headers": { ... },
     "quota_endpoint": { "url": "...", "status": 200, "body": { ... } },
     "error": null
   }
   ```

### Provider quota endpoints to hit

| Provider           | Endpoint                                         | Notes                          |
|--------------------|--------------------------------------------------|--------------------------------|
| Google AI Studio   | none reliable on free tier                       | rely on local tracking         |
| DeepSeek           | `GET /user/balance`                              | returns remaining credit       |
| Moonshot           | `GET /v1/users/me/balance` (verify)              |                                |
| Cerebras           | none — headers carry remaining                   |                                |
| SambaNova          | none — headers (verify)                          |                                |
| NVIDIA NIM         | `GET https://api.nvcf.nvidia.com/...` dashboard  | likely no programmatic credit  |
| Groq               | none — headers carry remaining                   |                                |
| OpenRouter         | `GET /api/v1/auth/key`, `GET /api/v1/credits`    | both useful                    |
| Hugging Face       | none reliable                                    | dashboard only                 |

## quota_drain.py (optional, run sparingly)

Goal: confirm 429 behavior + Retry-After value. Burn ≤2% of daily quota.

For one model per provider:
1. Send N small requests in tight loop until 429 OR N=20.
2. Record response headers + body of 429.
3. Wait, retry once after Retry-After, confirm recovery.
4. Save same JSON shape with `drain_log: [...]`.

Skip for opaque providers if quota is precious.

## findings.md output

Aggregator script (manual at this stage) reads all JSONs and writes `../findings.md`:

```
| Provider | Model | OK? | Latency | RPM hdr | RPD hdr | TPM hdr | TPD hdr | Cooldown on 429 | Verdict |
```

Verdict = keep / probation / drop, with one-line reason.
