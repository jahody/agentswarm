# agentSwarm

Pool of free-tier LLM providers routed by capability tier + quota. Local-first, single-host, OpenAI-compatible. 7 providers / 40+ models.

## Quick start

```bash
pip install -r requirements.txt
cp scripts/.env.example scripts/.env
$EDITOR scripts/.env                        # fill in API keys

python scripts/probe.py                    # connectivity check
python bank.py --tier T2 --prompt "hello" --max-tokens 256
```

## Provider signup (no credit card required)

Fill any subset; bank silently skips providers without keys. Minimum viable: Cerebras + Groq + OpenRouter.

| Env var                | Provider          | Get key here                                        |
|------------------------|-------------------|-----------------------------------------------------|
| `CEREBRAS_KEY`         | Cerebras          | https://cloud.cerebras.ai/                          |
| `GROQ_KEY`             | Groq              | https://console.groq.com/keys                       |
| `OPENROUTER_KEY`       | OpenRouter        | https://openrouter.ai/settings/keys                 |
| `GOOGLE_AI_STUDIO_KEY` | Google AI Studio  | https://aistudio.google.com/apikey                  |
| `SAMBANOVA_KEY`        | SambaNova         | https://cloud.sambanova.ai/                         |
| `NVIDIA_NIM_KEY`       | NVIDIA NIM        | https://build.nvidia.com/                           |
| `HF_TOKEN`             | HuggingFace       | https://huggingface.co/settings/tokens (Read scope) |
| `DEEPSEEK_KEY`         | DeepSeek          | paid-only as of 2026-05-03 — skip                   |
| `MOONSHOT_KEY`         | Moonshot (Kimi)   | paid-only as of 2026-05-03 — skip                   |

For OpenRouter `:free` slugs: enable https://openrouter.ai/settings/privacy → "free models privacy" toggle.

## CLI

```bash
# Connectivity probe
python scripts/probe.py
python scripts/probe.py groq cerebras --retries 1

# Capability sniff
python scripts/capsniff.py --tier T1     # T1 / T2 / T3
python scripts/capsniff.py groq

# One-shot inference
python bank.py --tier T2 --prompt "..." --max-tokens 256
python bank.py --tier T1 --prefer huggingface --max-tokens 1024
python bank.py --tier T3 --stream --prompt "count to 5" --max-tokens 32
python bank.py --tier any --prefer-models deepseek-ai/DeepSeek-R1 --prompt "..."

# Catalog sync (hits /v1/models, auto-disables stale IDs)
python bank.py --discover

# Health snapshot: status, quota, latency p50, strengths
python bank.py --status
```

**Tiers**: T1 = reasoning/quality-first · T2 = general/quota-first · T3 = fast/latency-p50 (Groq/Cerebras win).

## MCP server (Claude Code integration)

`mcp_server.py` wraps the bank so Claude Code can delegate cheap subtasks (summaries, transforms, docstring drafts) to free-tier models. Three tools: `llm_call`, `bank_status`, `discover_models`.

Add to `~/.claude.json` to make it available across all Claude Code projects (use the absolute path to `mcp_server.py`):

```json
{
  "mcpServers": {
    "agentswarm-bank": {
      "type": "stdio",
      "command": "python",
      "args": ["/absolute/path/to/agentswarm/mcp_server.py"]
    }
  }
}
```

For per-project setup only, add the same block to `.mcp.json` in the repo root with `"args": ["mcp_server.py"]`.

`llm_call` accepts `file_path` to read a local file server-side — avoids inlining large files into the prompt. See `CLAUDE.md` for delegation guidelines.

## Layout

```
agentSwarm/
  bank.py                    # Bank class, routing, failover, SQLite state
  mcp_server.py              # MCP stdio server (llm_call, bank_status, discover_models)
  bank-design.md             # architecture + decisions
  findings.md                # probe + capsniff verdicts
  roadmap.md                 # follow-on phases
  requirements.txt
  scripts/
    .env.example / .env      # API keys
    providers.yaml           # provider registry
    probe.py                 # connectivity + header capture → findings/*.json
    capsniff.py              # capability sniff (reasoning + code prompts)
  findings/                  # probe + capsniff JSON output (gitignored)
  bank-state.sqlite          # live state (gitignored)
```

## Troubleshooting

- **All providers skipped** — `.env` not in `scripts/`. Run `cp scripts/.env.example scripts/.env`.
- **`BankExhausted`** — every model in the tier is in cooldown or disabled. Run `bank.py --status` to see state.
- **OpenRouter `:free` returns 404** — enable free-models privacy toggle at https://openrouter.ai/settings/privacy.
- **Stale catalog** — run `bank.py --discover` then check the `disabled_models` table.

## License

MIT — see [LICENSE](LICENSE).
