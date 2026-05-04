# agentSwarm roadmap (optional next phases)

Recorded 2026-05-03 after Phase 1+2+3 complete. No commitment timeline.
Each item is independently shippable.

Ordered by ROI. Top items unlock more usage; bottom items polish.

**Status as of 2026-05-04:** Items 1, 3, and 4 shipped in `bank.py`.

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

---

## Suggested ordering (remaining)

1. **Weekly cron** (low effort, high signal-to-noise).
2. **Cost accounting** (only if paid fallback added).
3. **Package layout** (last — best done after API stabilizes).

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
