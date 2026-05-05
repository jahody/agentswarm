# agentSwarm roadmap (optional next phases)

Recorded 2026-05-03 after Phase 1+2+3 complete. No commitment timeline.
Each item is independently shippable.

Ordered by ROI. Top items unlock more usage; bottom items polish.

**Status as of 2026-05-04:** Items 1, 3, and 4 shipped in `bank.py`.

**Update 2026-05-05:** Gap audit added items 7–11 (Phase 2 candidates) below.
Items 2, 5, 6 deferred behind 7–11 in revised ordering.
**Same day:** items 7, 8, 9, 10, 11 shipped — see headings.

---

## ✅ 1. Latency-aware T3 routing (shipped 2026-05-04)

**Goal**: T3 selects fastest model first, not yaml-priority first. T3
SLA is "snap fast" — Groq llama-3.1-8b-instant 250 ms beats HF
gemma-3-27b 4 s, even when HF has more remaining quota.

**Approach**:
- Add column `latency_p50_ms` to `live` table.
- After each successful 200, update p50 with EWMA: `p50 = 0.8*p50 + 0.2*latency_ms`.
- In `_score()`, when `tier == "T3"`, replace tier-priority list with
  `-p50_ms` so lowest latency wins.
- Optional: store separate p50 per (provider, model) and per recent
  hour-bucket; ignore samples older than 24 h.

**Files**:
- `bank.py`: `StateStore._init_schema` (ALTER TABLE), `set_state` writes
  p50, `_score` switches on tier, `_call` updates p50 on 200.

**Complexity**: ~40 lines. Schema migration: `ALTER TABLE live ADD COLUMN latency_p50_ms REAL`. Idempotent on existing DB.

**Verify**:
- 50 successive `bank.chat(tier="T3")` calls.
- Sort `live` table by `latency_p50_ms` ASC — top entries should be Groq + Cerebras 8b.
- `bank.acquire(tier="T3")` returns Groq llama-3.1-8b-instant in >80% of pulls.

**Risk**: cold-start values inflate p50 unfairly. Mitigation: don't update p50 on the first call after >5 min idle (skew flag).

---

## 2. Weekly cron re-runs (probe + capsniff)

**Status**: not started. `scripts/weekly.sh` and `scripts/diff_capsniff.py`
named below do not yet exist; only `probe.py` and `capsniff.py` ship today.

**Goal**: catch quality regressions and recovered upstream routes
without manual prompting. Free providers churn weekly.

**Approach**:
- Add `scripts/weekly.sh` that runs in this order:
  1. `python3 scripts/probe.py --skip-quota` (cheap connectivity)
  2. `python3 bank.py --discover` (refresh catalogs, auto-disable stale)
  3. `python3 scripts/capsniff.py --tier T1`
  4. `python3 scripts/capsniff.py --tier T2`
  5. `python3 scripts/capsniff.py --tier T3`
  6. Diff this week's capsniff JSON vs last week's; print verdict
     changes (PASS→FAIL, ERR→PASS, etc.).
- User adds to crontab: `0 6 * * 0 /path/to/weekly.sh > /tmp/agentswarm-weekly.log 2>&1`.

**Files**:
- New `scripts/weekly.sh`.
- New `scripts/diff_capsniff.py`: reads last-2-weeks of `findings/capsniff/*.json`, groups by (provider, model, prompt), reports changed verdicts.

**Complexity**: ~80 lines.

**Verify**: dry-run with `--no-cron`; observed log shows all phases ran and diff renders even when no change.

**Risk**: weekly capsniff burns ~70 token-cheap calls × ~7 retries worst-case = ~500 calls. Across SambaNova (20 RPD/model) that's 17 days of budget for one model. Mitigation: skip SambaNova in weekly capsniff; full-rerun monthly.

---

## ✅ 3. Multi-process safety (shipped 2026-05-04)

**Goal**: allow multiple agentSwarm clients (e.g. concurrent jobs)
without state corruption.

**Current**: SQLite WAL allows multi-reader, single-writer. State
mutations use `INSERT OR REPLACE` which is not race-safe (one process
can clobber another's `tpm_remaining` decrement).

**Approach**:
- Wrap state mutations in transactions: `BEGIN IMMEDIATE; ... COMMIT;`.
- Convert `set_state` to upsert with `WHERE excluded.updated_at >= live.updated_at` to skip stale writes.
- Add `process_id` column to `events` for audit.

**Files**: `bank.py` `StateStore` methods only.

**Complexity**: ~30 lines, plus migration.

**Verify**: spawn 4 workers each calling `bank.chat()` 50× concurrently
against Groq. Assert `events` table count = 200. Assert
`live.rpm_remaining` matches Groq's reported headers within ±2.

**Risk**: deadlocks under high contention. Mitigation: SQLite's
`busy_timeout` set to 30 s in `_conn`.

---

## ✅ 4. Tool calling / function calling (shipped 2026-05-04)

**Goal**: pass-through OpenAI-compat `tools` field. Bank already speaks
OAI; just need to forward + parse `tool_calls` in response.

**Approach**:
- Add `tools: list | None = None` and `tool_choice: str | dict | None = None` to `chat()` and `stream()`. Forward in payload.
- In `BankResponse`, expose `tool_calls = body.choices[0].message.tool_calls` if present.
- Failover policy: 400 with "tools not supported" → mark
  `model.supports_tools = false` in DB; future routing skips that model
  when `tools` set.

**Files**: `bank.py` (`chat`, `stream`, `BankResponse`, schema column on `models`).

**Complexity**: ~50 lines.

**Verify**: send a `weather` tool to Groq llama-3.3-70b; assert
`resp.tool_calls[0].function.name == "weather"`. Then send to a model
known to lack tool support (e.g. llama-3.1-8b on cerebras) and assert
`model.supports_tools` flipped false after one 400.

**Risk**: providers vary on tool-call schema (some return `function_call`, some `tool_calls`, some both). Need normalization layer.

---

## 5. Cost accounting

**Goal**: track $-equivalent spend even on free tiers. Useful when
`prefer` would otherwise pick a paid-leaning model.

**Approach**:
- New `pricing.yaml`: per-(provider, model) `prompt_per_mtok` and
  `completion_per_mtok` (USD).
- Bank reads on init, attaches to `Model`.
- After each 200, compute `cost = (prompt_tokens/1e6)*prompt_price + (completion_tokens/1e6)*completion_price`.
- Add `cost_usd` column to `events`.
- New `bank.cost_summary(since=ts)` aggregates per model/provider.

**Files**: new `pricing.yaml`, `bank.py` (load + record + summary), `findings.md` reference.

**Complexity**: ~80 lines + populating pricing table (look up published rates).

**Verify**: 5 chat calls; `bank.cost_summary()` returns per-provider $-totals matching hand-calc within 1%.

**Risk**: free tiers nominally $0. Useful only if paid fallback added (DeepSeek/Moonshot if user funds).

---

## 6. Package layout

**Goal**: `pip install -e .` so other projects can `from agent_swarm import Bank`.

**Approach**:
- `agentSwarm/pyproject.toml` with `name = "agent-swarm"`, deps `httpx, pyyaml, python-dotenv`.
- Move `bank.py` → `src/agent_swarm/bank.py` or `agent_swarm/bank.py`.
- Move `scripts/providers.yaml` → `agent_swarm/data/providers.yaml`,
  bundled via `package-data`.
- Keep `scripts/probe.py`, `scripts/capsniff.py` as separate CLI entry
  points: `[project.scripts] swarm-probe = "agent_swarm.cli:probe"` etc.
- `from agent_swarm import Bank; bank = Bank.from_yaml()` (default yaml
  path = bundled data).

**Files**: new `pyproject.toml`, restructure into `agent_swarm/` package, update imports in scripts.

**Complexity**: ~120 lines + import surgery. Mostly mechanical.

**Verify**: `pip install -e .`; `python -c "from agent_swarm import Bank; b = Bank.from_yaml(); print(b.chat(messages=[{'role':'user','content':'pong'}], tier='T3').text)"`.

**Risk**: minor — restructure breaks current scripts/ relative imports. Update at same time.

**API-freeze checklist (prerequisite)**: before publishing, decide on
(a) `Bank.from_yaml` accepting `Path`/file-like, (b) collapsing the 9
keyword args on `chat`/`stream` into a `RequestOptions` dataclass or leaving
as-is, (c) typing `BankResponse.meta` as a `TypedDict` instead of free-form
`dict`. Without this, v0→v1 churn lands on every consumer.

---

## Phase 2 candidates (added 2026-05-05 after gap audit)

Independently shippable. Listed in priority order; insert ahead of remaining
Phase 1 items (#2 weekly cron, #5 cost, #6 packaging).

---

## ✅ 7. Automated tests + CI (shipped 2026-05-05)

**Goal**: protect failover, header-parsing, and StateStore concurrency from
silent regressions. Today there are zero tests for a stateful library with 6
header parsers, multi-process SQLite, and a 6-step failover state machine.
Item 3's verification ("spawn 4 workers...") has no harness.

**Approach**:
- `tests/test_score.py`: feed mock `Model`+`Live` rows, assert tier ordering
  and T3 latency-first behavior.
- `tests/test_failover.py`: `respx`/`httpx_mock` simulating
  429→cooldown→next, 401→provider disable, 404→model disable,
  400+"tool"→supports_tools=false.
- `tests/test_state.py`: 4 threads × 50 upserts; assert no torn writes,
  stale-write guard rejects out-of-order updates.
- `tests/test_headers.py`: golden-file replay of recorded headers per provider
  (groq, cerebras, sambanova, huggingface, openrouter, google_ai_studio).
- `.github/workflows/test.yml`: pytest on push.

**Files**: new `tests/` tree, `pytest.ini`, `respx` in `requirements-dev.txt`.

**Complexity**: ~400 lines test + ~30 lines CI.

**Verify**: `pytest -q` green; CI badge in README.

**Risk**: header parsers are brittle if upstream wire format changes.
Mitigation: golden files dated in filename, easy to refresh.

---

## ✅ 8. Tool-call schema normalization (shipped 2026-05-05)

**Goal**: item 4 ships pass-through but flagged "providers vary on tool-call
schema (some return `function_call`, some `tool_calls`, some both)" as
unaddressed. Centralize parsing so callers see one shape.

**Approach**:
- `_normalize_tool_calls(message)`: read `tool_calls` (OpenAI 1.0+),
  `function_call` (legacy), and provider-specific structures; return canonical
  `[{id, name, arguments_json}, ...]`.
- Apply in both `chat` and the streaming accumulator (item 11).
- Per-model dialect override: `models.tool_call_dialect = "openai"|"legacy"`.

**Files**: `bank.py` (`BankResponse`, `_normalize_tool_calls`).

**Complexity**: ~50 lines.

**Verify**: live `weather` tool call to Groq + Cerebras + OpenRouter; assert
`resp.tool_calls[0].name == "weather"` across all three.

**Risk**: low.

---

## ✅ 9. JSON mode / structured output (shipped 2026-05-05)

**Goal**: pass-through OpenAI-compat `response_format`. Groq, OpenRouter, and
Google AI Studio support it. Useful for the extraction/transform tasks the MCP
`llm_call` is already pointed at.

**Approach**:
- Add `response_format: dict | None = None` to `chat()` and `stream()`.
- Forward in payload.
- Failover: 400 with body containing "response_format" → mark
  `model.supports_json = false`; future routing skips when set. Mirror of how
  `supports_tools` is tracked.
- Schema: `ALTER TABLE models ADD COLUMN supports_json INTEGER DEFAULT 1`.

**Files**: `bank.py` (signatures, payload, failover policy, schema).

**Complexity**: ~30 lines + migration.

**Verify**: ask Groq llama-3.3-70b for `{"type": "json_object"}`; assert
`json.loads(resp.text)` succeeds. Then ask a model lacking support; assert
`supports_json` flips false after one 400.

**Risk**: low. Cousin of item 4.

---

## ✅ 10. MCP server first-class (shipped 2026-05-05)

**Goal**: `mcp_server.py` is built, exposes 3 tools (`llm_call`,
`bank_status`, `discover_models`), and is advertised in CLAUDE.md — but is
untracked in git and absent from the roadmap. Status is undeclared.

**Approach**:
- Commit `mcp_server.py`.
- README section: install, register with Claude Code
  (`claude mcp add agentswarm-bank ...`), document the three tools.
- Pin MCP SDK version in `requirements.txt`.

**Files**: `mcp_server.py`, `README.md`, `requirements.txt`.

**Complexity**: trivial — code already works.

**Verify**: fresh clone → `claude mcp add ...` →
`mcp__agentswarm-bank__bank_status` returns within Claude Code.

**Risk**: none.

---

## ✅ 11. Streaming tool calls (shipped 2026-05-05)

**Goal**: today `stream=True` together with `tools=[...]` is not supported —
SSE deltas containing `tool_calls` chunks are dropped on the floor. Item 1
shipped streaming, item 4 shipped tools; the union is the gap.

**Approach**:
- In `stream()` SSE accumulator, when `delta.tool_calls[i]` chunk arrives,
  append to a per-index buffer; emit assembled tool_calls when stream closes
  or `finish_reason == "tool_calls"`.
- Yield `BankStreamChunk(type="tool_call_delta"|"text"|"done", ...)`.
- Reuse normalizer from item 8.

**Files**: `bank.py` (`stream` method only).

**Complexity**: ~80 lines.

**Verify**: `bank.stream(messages, tools=[weather_tool])` against Groq
llama-3.3-70b; assert assembled `tool_calls[0].name == "weather"` matches the
non-streaming response for the same prompt.

**Risk**: SSE chunk indexing varies across providers. Mitigation: restrict
initial support to Groq + OpenRouter (most OpenAI-faithful), gate others
behind `supports_streaming_tools` flag.

---

## Suggested ordering (revised 2026-05-05)

1. **Tests + CI** (#7) — single highest-ROI absence.
2. **Tool-call normalization** (#8) — close out item 4's known risk.
3. **JSON mode** (#9) — small surface, high user value.
4. **MCP server first-class** (#10) — already built; commit + document.
5. **Streaming tool calls** (#11) — natural completion of items 1 + 4.
6. **Weekly cron** (#2) — low effort, high signal-to-noise.
7. **Cost accounting** (#5) — only if paid fallback added.
8. **Package layout** (#6) — last; best done after API stabilizes.

Skip cost accounting entirely if no plan to add paid providers.

---

## Out of scope (deliberately not on this roadmap)

- Multi-account pooling: TOS gray zone, declared out of scope in
  `research-plan.md`.
- Web reverse-engineered endpoints (Duck.ai etc.): fragile, declared
  out in `research-plan.md`.
- On-prem inference: hardware not available.
- UI/dashboard: console output is sufficient; if dashboard needed,
  re-use `AgentMaster/` sibling project.
