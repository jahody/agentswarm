"""Agent Bank — pool of free-tier LLM providers picked by tier + remaining quota.

Public surface (matches bank-design.md):

    from agentSwarm.bank import Bank
    bank = Bank.from_yaml("scripts/providers.yaml", state="bank-state.sqlite")
    resp = bank.chat(
        messages=[{"role": "user", "content": "hello"}],
        tier="T2",
        max_tokens=256,
        prefer=["groq"],          # optional
        avoid=["nvidia_nim"],     # optional
    )
    print(resp.text, "via", resp.provider, resp.model, resp.latency_ms, "ms")

Single-process, single-host. SQLite WAL for state. Header-trusted where
exposed; local token bucket where opaque (Google AI Studio, NVIDIA NIM).
"""
from __future__ import annotations

import os
import sys
import time
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

import httpx
import yaml
from dotenv import load_dotenv

# Tier priority encodes the post-Phase-1 selection order from findings.md +
# bank-design.md. Earlier in the list = higher preference.
TIER_PRIORITY: dict[str, list[str]] = {
    "T1": ["huggingface", "nvidia_nim", "sambanova", "cerebras", "openrouter"],
    "T2": ["groq", "huggingface", "sambanova", "google_ai_studio", "nvidia_nim", "openrouter"],
    "T3": ["groq", "cerebras", "google_ai_studio", "sambanova", "huggingface", "openrouter"],
}


# ---------- dataclasses ----------

@dataclass
class Model:
    provider: str
    model_id: str
    tier: str  # T1 | T2 | T3
    doc_rpm: int | None = None
    doc_rpd: int | None = None
    doc_tpm: int | None = None
    doc_tpd: int | None = None
    supports_tools: bool = True
    benchmarks: dict | None = None  # {"mmlu": 86.4, ...} loaded from benchmarks.yaml
    strengths: list[str] = field(default_factory=list)  # e.g. ["coding", "reasoning"]


# Maps provider-specific model IDs to canonical HuggingFace Hub IDs for benchmark lookup.
# None = model has no HF Hub presence (e.g. Gemini, gpt-oss).
CANONICAL_HF_IDS: dict[str, str | None] = {
    "llama3.1-8b":                            "meta-llama/Llama-3.1-8B-Instruct",
    "llama-3.3-70b-versatile":                "meta-llama/Llama-3.3-70B-Instruct",
    "llama-3.1-8b-instant":                   "meta-llama/Llama-3.1-8B-Instruct",
    "Meta-Llama-3.3-70B-Instruct":            "meta-llama/Llama-3.3-70B-Instruct",
    "Llama-4-Maverick-17B-128E-Instruct":     "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
    "meta/llama-3.3-70b-instruct":            "meta-llama/Llama-3.3-70B-Instruct",
    "DeepSeek-V3.2":                          "deepseek-ai/DeepSeek-V3-0324",
    "DeepSeek-V3.1":                          "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.2":              "deepseek-ai/DeepSeek-V3-0324",
    "qwen-3-235b-a22b-instruct-2507":         "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "qwen/qwen3-32b":                         "Qwen/Qwen3-32B",
    "Qwen/Qwen3-Next-80B-A3B-Instruct":       "Qwen/Qwen3-30B-A3B",
    "qwen/qwen3-next-80b-a3b-instruct":       "Qwen/Qwen3-30B-A3B",
    "gemma-3-12b-it":                         "google/gemma-3-12b-it",
    "moonshotai/kimi-k2-instruct":            "moonshotai/Kimi-K2-Instruct",
    "moonshotai/kimi-k2-thinking":            "moonshotai/Kimi-K2-Thinking",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "nvidia/Llama-3.3-Nemotron-Super-49B-v1",
    "z-ai/glm5":                              "THUDM/GLM-4-9B-Chat",
    "z-ai/glm-4.5-air:free":                  None,
    "gemini-2.5-flash":                       None,
    "gemini-2.5-flash-lite":                  None,
    "gpt-oss-120b":                           None,
    "openai/gpt-oss-120b":                    None,
    "openai/gpt-oss-120b:free":               None,
    "openai/gpt-oss-20b":                     None,
    "openai/gpt-oss-20b:free":                None,
    "deepseek-ai/deepseek-v4-pro":            None,
    "deepseek-ai/deepseek-v4-flash":          None,
    "minimax/minimax-m2.5:free":              None,
    "nvidia/nemotron-3-super-120b-a12b:free": None,
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": None,
}


@dataclass
class Provider:
    name: str
    base_url: str
    key_env: str
    quota_endpoint: str | None
    quota_strategy: str           # headers | opaque | shared_bucket
    shared_bucket: bool
    request_timeout: float
    data_used_for_training: bool
    models: list[Model]
    disabled_models: dict[str, str]   # model_id -> reason


@dataclass
class LiveState:
    provider: str
    model_id: str   # "*" for shared-bucket providers
    rpm_remaining: int | None = None
    rpd_remaining: int | None = None
    tpm_remaining: int | None = None
    tpd_remaining: int | None = None
    tpm_limit: int | None = None
    tpd_limit: int | None = None
    rpm_limit: int | None = None
    rpd_limit: int | None = None
    cooldown_until: float = 0.0
    last_429_at: float = 0.0
    last_success_at: float = 0.0
    updated_at: float = 0.0
    latency_p50_ms: float | None = None


@dataclass
class Lease:
    provider: Provider
    model: Model
    state_key: str   # model_id or "*"


@dataclass
class BankResponse:
    body: dict | str
    provider: str
    model: str
    latency_ms: int
    status: int
    headers: dict
    text: str | None
    tool_calls: list | None = None
    meta: dict = field(default_factory=dict)
    # meta keys when populated:
    #   prompt_tokens, completion_tokens, total_tokens   (from response usage)
    #   rpm_remaining, rpd_remaining, tpm_remaining      (post-call live state)
    #   tier                                              (the requested tier)
    #   attempts                                          (failover count, 1 = first try)


# ---------- exceptions ----------

class BankError(Exception): pass
class BankExhausted(BankError): pass
class _Retryable429(BankError):
    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
class _RetryableTransient(BankError):
    pass
class _PermanentDisable(BankError):
    def __init__(self, reason: str): self.reason = reason


# ---------- state store ----------

class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._init_schema()

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def _transaction(self):
        """Write transaction with process-level lock and BEGIN IMMEDIATE."""
        with self.lock:
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                yield c
                c.execute("COMMIT")
            except Exception:
                try:
                    c.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                c.close()

    @contextmanager
    def _read(self):
        """Read-only query — no write lock needed."""
        c = self._conn()
        try:
            yield c
        finally:
            c.close()

    def _init_schema(self):
        with self.lock:
            c = self._conn()
            try:
                c.executescript("""
                CREATE TABLE IF NOT EXISTS providers(
                    name TEXT PRIMARY KEY,
                    disabled INTEGER DEFAULT 0,
                    disabled_reason TEXT,
                    last_check_at REAL
                );
                CREATE TABLE IF NOT EXISTS models(
                    provider TEXT, model_id TEXT, tier TEXT,
                    doc_rpm INTEGER, doc_rpd INTEGER, doc_tpm INTEGER, doc_tpd INTEGER,
                    PRIMARY KEY(provider, model_id)
                );
                CREATE TABLE IF NOT EXISTS live(
                    provider TEXT, model_id TEXT,
                    rpm_remaining INTEGER, rpd_remaining INTEGER,
                    tpm_remaining INTEGER, tpd_remaining INTEGER,
                    cooldown_until REAL, last_429_at REAL, last_success_at REAL,
                    updated_at REAL,
                    PRIMARY KEY(provider, model_id)
                );
                CREATE TABLE IF NOT EXISTS events(
                    ts REAL, provider TEXT, model_id TEXT,
                    status INTEGER, latency_ms INTEGER, error TEXT
                );
                CREATE INDEX IF NOT EXISTS events_ts_idx ON events(ts);
                CREATE TABLE IF NOT EXISTS disabled_models(
                    provider TEXT, model_id TEXT, reason TEXT, since_ts REAL,
                    PRIMARY KEY(provider, model_id)
                );
                """)
                for stmt in [
                    "ALTER TABLE live ADD COLUMN latency_p50_ms REAL",
                    "ALTER TABLE models ADD COLUMN supports_tools INTEGER DEFAULT 1",
                    "ALTER TABLE events ADD COLUMN process_id INTEGER",
                    "ALTER TABLE models ADD COLUMN supports_json INTEGER DEFAULT 1",
                    "ALTER TABLE live ADD COLUMN tpm_limit INTEGER",
                    "ALTER TABLE live ADD COLUMN tpd_limit INTEGER",
                    "ALTER TABLE live ADD COLUMN rpm_limit INTEGER",
                    "ALTER TABLE live ADD COLUMN rpd_limit INTEGER",
                ]:
                    try:
                        c.execute(stmt)
                    except sqlite3.OperationalError:
                        pass
            finally:
                c.close()

    def upsert_provider(self, name: str):
        with self._transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO providers(name, last_check_at) VALUES (?, ?)",
                (name, time.time()),
            )

    def upsert_model(self, m: Model):
        with self._transaction() as c:
            c.execute("""
                INSERT OR REPLACE INTO models(provider, model_id, tier, doc_rpm, doc_rpd, doc_tpm, doc_tpd)
                VALUES (?,?,?,?,?,?,?)
            """, (m.provider, m.model_id, m.tier, m.doc_rpm, m.doc_rpd, m.doc_tpm, m.doc_tpd))

    def get_state(self, provider: str, model_id: str) -> LiveState:
        with self._read() as c:
            row = c.execute(
                "SELECT * FROM live WHERE provider=? AND model_id=?",
                (provider, model_id),
            ).fetchone()
            if row is None:
                return LiveState(provider=provider, model_id=model_id)
            return LiveState(
                provider=row["provider"],
                model_id=row["model_id"],
                rpm_remaining=row["rpm_remaining"],
                rpd_remaining=row["rpd_remaining"],
                tpm_remaining=row["tpm_remaining"],
                tpd_remaining=row["tpd_remaining"],
                tpm_limit=row["tpm_limit"],
                tpd_limit=row["tpd_limit"],
                rpm_limit=row["rpm_limit"],
                rpd_limit=row["rpd_limit"],
                cooldown_until=row["cooldown_until"] or 0.0,
                last_429_at=row["last_429_at"] or 0.0,
                last_success_at=row["last_success_at"] or 0.0,
                updated_at=row["updated_at"] or 0.0,
                latency_p50_ms=row["latency_p50_ms"],
            )

    def get_doc_limits(self, provider: str, model_id: str) -> dict:
        with self._read() as c:
            row = c.execute(
                "SELECT doc_rpm, doc_rpd, doc_tpm, doc_tpd FROM models WHERE provider=? AND model_id=?",
                (provider, model_id),
            ).fetchone()
        if row is None:
            return {"doc_rpm": None, "doc_rpd": None, "doc_tpm": None, "doc_tpd": None}
        return {"doc_rpm": row["doc_rpm"], "doc_rpd": row["doc_rpd"],
                "doc_tpm": row["doc_tpm"], "doc_tpd": row["doc_tpd"]}

    def set_state(self, s: LiveState):
        s.updated_at = time.time()
        with self._transaction() as c:
            c.execute("""
                INSERT INTO live(
                    provider, model_id,
                    rpm_remaining, rpd_remaining, tpm_remaining, tpd_remaining,
                    tpm_limit, tpd_limit, rpm_limit, rpd_limit,
                    cooldown_until, last_429_at, last_success_at, updated_at,
                    latency_p50_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider, model_id) DO UPDATE SET
                    rpm_remaining=excluded.rpm_remaining,
                    rpd_remaining=excluded.rpd_remaining,
                    tpm_remaining=excluded.tpm_remaining,
                    tpd_remaining=excluded.tpd_remaining,
                    tpm_limit=coalesce(excluded.tpm_limit, live.tpm_limit),
                    tpd_limit=coalesce(excluded.tpd_limit, live.tpd_limit),
                    rpm_limit=coalesce(excluded.rpm_limit, live.rpm_limit),
                    rpd_limit=coalesce(excluded.rpd_limit, live.rpd_limit),
                    cooldown_until=excluded.cooldown_until,
                    last_429_at=excluded.last_429_at,
                    last_success_at=excluded.last_success_at,
                    latency_p50_ms=excluded.latency_p50_ms,
                    updated_at=excluded.updated_at
                WHERE excluded.updated_at >= live.updated_at
            """, (
                s.provider, s.model_id,
                s.rpm_remaining, s.rpd_remaining, s.tpm_remaining, s.tpd_remaining,
                s.tpm_limit, s.tpd_limit, s.rpm_limit, s.rpd_limit,
                s.cooldown_until, s.last_429_at, s.last_success_at, s.updated_at,
                s.latency_p50_ms,
            ))

    def set_cooldown(self, provider: str, model_id: str, until_ts: float, on_429: bool):
        cur = self.get_state(provider, model_id)
        cur.cooldown_until = max(cur.cooldown_until, until_ts)
        if on_429:
            cur.last_429_at = time.time()
        self.set_state(cur)

    def log_event(self, provider: str, model_id: str, status: int, latency_ms: int, error: str | None):
        with self._transaction() as c:
            c.execute(
                "INSERT INTO events(ts, provider, model_id, status, latency_ms, error, process_id) VALUES (?,?,?,?,?,?,?)",
                (time.time(), provider, model_id, status, latency_ms, error, os.getpid()),
            )

    def disable_model(self, provider: str, model_id: str, reason: str):
        with self._transaction() as c:
            c.execute("""
                INSERT OR REPLACE INTO disabled_models(provider, model_id, reason, since_ts)
                VALUES (?,?,?,?)
            """, (provider, model_id, reason, time.time()))

    def is_model_disabled(self, provider: str, model_id: str) -> bool:
        with self._read() as c:
            row = c.execute(
                "SELECT 1 FROM disabled_models WHERE provider=? AND model_id=?",
                (provider, model_id),
            ).fetchone()
            return row is not None

    def disable_provider(self, name: str, reason: str):
        with self._transaction() as c:
            c.execute(
                "UPDATE providers SET disabled=1, disabled_reason=? WHERE name=?",
                (reason, name),
            )

    def is_provider_disabled(self, name: str) -> bool:
        with self._read() as c:
            row = c.execute("SELECT disabled FROM providers WHERE name=?", (name,)).fetchone()
            return bool(row and row["disabled"])

    def mark_no_tools(self, provider: str, model_id: str):
        with self._transaction() as c:
            c.execute(
                "UPDATE models SET supports_tools=0 WHERE provider=? AND model_id=?",
                (provider, model_id),
            )

    def get_supports_tools(self, provider: str, model_id: str) -> bool:
        with self._read() as c:
            row = c.execute(
                "SELECT supports_tools FROM models WHERE provider=? AND model_id=?",
                (provider, model_id),
            ).fetchone()
            return bool(row["supports_tools"]) if row else True

    def mark_no_json(self, provider: str, model_id: str):
        with self._transaction() as c:
            c.execute(
                "UPDATE models SET supports_json=0 WHERE provider=? AND model_id=?",
                (provider, model_id),
            )

    def get_supports_json(self, provider: str, model_id: str) -> bool:
        with self._read() as c:
            row = c.execute(
                "SELECT supports_json FROM models WHERE provider=? AND model_id=?",
                (provider, model_id),
            ).fetchone()
            return bool(row["supports_json"]) if row else True


# ---------- header parsers ----------

def _to_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_cerebras(h: dict) -> dict:
    return {
        "rpm_remaining": _to_int(h.get("x-ratelimit-remaining-requests-minute")),
        "rpd_remaining": _to_int(h.get("x-ratelimit-remaining-requests-day")),
        "tpm_remaining": _to_int(h.get("x-ratelimit-remaining-tokens-minute")),
        "tpd_remaining": _to_int(h.get("x-ratelimit-remaining-tokens-day")),
        "rpm_limit":     _to_int(h.get("x-ratelimit-limit-requests-minute")),
        "rpd_limit":     _to_int(h.get("x-ratelimit-limit-requests-day")),
        "tpm_limit":     _to_int(h.get("x-ratelimit-limit-tokens-minute")),
        "tpd_limit":     _to_int(h.get("x-ratelimit-limit-tokens-day")),
    }


def parse_groq(h: dict) -> dict:
    # Window inferred from reset interval; we just keep current "remaining"
    # as RPM/TPM (good enough for soft selection; 429 is authoritative).
    return {
        "rpm_remaining": _to_int(h.get("x-ratelimit-remaining-requests")),
        "tpm_remaining": _to_int(h.get("x-ratelimit-remaining-tokens")),
        "rpm_limit":     _to_int(h.get("x-ratelimit-limit-requests")),
        "tpm_limit":     _to_int(h.get("x-ratelimit-limit-tokens")),
    }


def parse_sambanova(h: dict) -> dict:
    return {"rpd_remaining": _to_int(h.get("x-ratelimit-remaining-requests-day"))}


def parse_huggingface(h: dict) -> dict:
    return {
        "rpm_remaining": _to_int(h.get("x-ratelimit-remaining-requests")),
        "tpm_remaining": _to_int(h.get("x-ratelimit-remaining-tokens")),
        "rpm_limit":     _to_int(h.get("x-ratelimit-limit-requests")),
        "tpm_limit":     _to_int(h.get("x-ratelimit-limit-tokens")),
    }


def parse_openrouter(h: dict) -> dict:
    return {"rpd_remaining": _to_int(h.get("x-ratelimit-remaining"))}


PARSERS: dict[str, Callable[[dict], dict]] = {
    "cerebras": parse_cerebras,
    "groq": parse_groq,
    "sambanova": parse_sambanova,
    "huggingface": parse_huggingface,
    "openrouter": parse_openrouter,
    "google_ai_studio": lambda h: {},
    "nvidia_nim": lambda h: {},
}


# ---------- tool-call normalization ----------

def _normalize_tool_calls(msg: dict) -> list | None:
    """Convert provider variations of tool-call output to canonical OpenAI list.

    Returns None if no tool calls. Output shape:
      [{"id": str, "type": "function",
        "function": {"name": str, "arguments": str}}, ...]
    `arguments` is always the model-emitted JSON string; caller `json.loads`.
    """
    tcs = msg.get("tool_calls")
    if isinstance(tcs, list) and tcs:
        out: list = []
        for i, tc in enumerate(tcs):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            if not name:
                continue
            args = fn.get("arguments")
            if args is None:
                args = tc.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args) if args is not None else "{}"
            out.append({
                "id": tc.get("id") or f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": args},
            })
        return out or None
    fc = msg.get("function_call")
    if isinstance(fc, dict) and fc.get("name"):
        args = fc.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args) if args is not None else "{}"
        return [{
            "id": "call_0",
            "type": "function",
            "function": {"name": fc["name"], "arguments": args},
        }]
    return None


# ---------- bank ----------

class Bank:
    def __init__(
        self,
        providers: list[Provider],
        state: StateStore,
        client: httpx.Client | None = None,
    ):
        self.providers = {p.name: p for p in providers}
        self.state = state
        self.client = client or httpx.Client()
        self._populate_state_tables()

    @classmethod
    def from_yaml(
        cls,
        yaml_path: str | Path,
        state: str | Path = "bank-state.sqlite",
        env_path: str | Path | None = None,
    ) -> "Bank":
        yaml_path = Path(yaml_path)
        if env_path is None:
            env_path = yaml_path.parent / ".env"
        load_dotenv(env_path)
        with yaml_path.open() as f:
            raw = yaml.safe_load(f)
        provs: list[Provider] = []
        for p in raw:
            models = []
            for m in p.get("models", []):
                if isinstance(m, str):
                    models.append(Model(provider=p["name"], model_id=m, tier="T2"))
                else:
                    models.append(Model(
                        provider=p["name"],
                        model_id=m["id"],
                        tier=m.get("tier", "T2"),
                        doc_rpm=m.get("doc_rpm"),
                        doc_rpd=m.get("doc_rpd"),
                        doc_tpm=m.get("doc_tpm"),
                        doc_tpd=m.get("doc_tpd"),
                        strengths=m.get("strengths", []),
                    ))
            disabled = {d["id"]: d.get("reason", "") for d in p.get("disabled_models", [])}
            provs.append(Provider(
                name=p["name"],
                base_url=p["base_url"].rstrip("/"),
                key_env=p["key_env"],
                quota_endpoint=p.get("quota_endpoint"),
                quota_strategy=p.get("quota_strategy", "opaque"),
                shared_bucket=bool(p.get("shared_bucket", False)),
                request_timeout=float(p.get("request_timeout", 60)),
                data_used_for_training=bool(p.get("data_used_for_training", False)),
                models=models,
                disabled_models=disabled,
            ))
        bm_path = yaml_path.parent / "benchmarks.yaml"
        if bm_path.exists():
            with bm_path.open() as f:
                bm_data = yaml.safe_load(f) or {}
            for p in provs:
                for m in p.models:
                    hf_id = CANONICAL_HF_IDS.get(m.model_id, m.model_id)
                    if hf_id:
                        m.benchmarks = bm_data.get(hf_id) or None
        return cls(provs, StateStore(Path(state)))

    def _populate_state_tables(self):
        for p in self.providers.values():
            self.state.upsert_provider(p.name)
            for m in p.models:
                self.state.upsert_model(m)
            for mid, reason in p.disabled_models.items():
                self.state.disable_model(p.name, mid, reason)

    # ---- selection ----

    def _state_key(self, p: Provider, m: Model) -> str:
        return "*" if p.shared_bucket else m.model_id

    def _score(
        self,
        p: Provider,
        m: Model,
        s: LiveState,
        prefer: list[str] | None,
        prefer_models: list[str] | None,
        tier: str,
    ) -> tuple:
        # Model-level prefer dominates provider-level prefer.
        model_bonus = 2 if (prefer_models and m.model_id in prefer_models) else 0
        prefer_bonus = 1 if (prefer and p.name in prefer) else 0
        rpm = s.rpm_remaining if s.rpm_remaining is not None else 1_000_000
        rpd = s.rpd_remaining if s.rpd_remaining is not None else 1_000_000
        tpm = s.tpm_remaining if s.tpm_remaining is not None else 1_000_000_000
        if tier == "T3":
            # Latency-first: lower p50 wins. Unknown p50 treated as worst-case.
            p50 = s.latency_p50_ms if s.latency_p50_ms is not None else 999_999.0
            return (model_bonus, prefer_bonus, -p50, rpd, rpm, tpm)
        prio_list = TIER_PRIORITY.get(tier, [])
        try:
            prio = -prio_list.index(p.name)
        except ValueError:
            prio = -999
        return (model_bonus, prefer_bonus, prio, rpd, rpm, tpm)

    def acquire(
        self,
        tier: str | None,
        prefer: list[str] | None = None,
        prefer_models: list[str] | None = None,
        avoid: set[str] | None = None,
        avoid_models: set[str] | None = None,
        min_tokens: int | None = None,
        requires_tools: bool = False,
        requires_json: bool = False,
        require_strengths: list[str] | None = None,
    ) -> Lease | None:
        now = time.time()
        avoid = avoid or set()
        avoid_models = avoid_models or set()
        candidates = []
        for p in self.providers.values():
            if p.name in avoid or self.state.is_provider_disabled(p.name):
                continue
            if not os.getenv(p.key_env):
                continue
            for m in p.models:
                if tier is not None and m.tier != tier:
                    continue
                if m.model_id in avoid_models:
                    continue
                if self.state.is_model_disabled(p.name, m.model_id):
                    continue
                if requires_tools and not self.state.get_supports_tools(p.name, m.model_id):
                    continue
                if requires_json and not self.state.get_supports_json(p.name, m.model_id):
                    continue
                key = self._state_key(p, m)
                s = self.state.get_state(p.name, key)
                if s.cooldown_until > now:
                    continue
                if (
                    min_tokens
                    and s.tpm_remaining is not None
                    and s.tpm_remaining < min_tokens
                ):
                    continue
                candidates.append(
                    (self._score(p, m, s, prefer, prefer_models, m.tier), p, m, key)
                )
        if require_strengths:
            matching = [c for c in candidates if any(s in c[2].strengths for s in require_strengths)]
            if matching:
                candidates = matching
        if not candidates:
            return None
        candidates.sort(reverse=True, key=lambda x: x[0])
        _, p, m, key = candidates[0]
        return Lease(provider=p, model=m, state_key=key)

    # ---- chat ----

    def chat(
        self,
        messages: list[dict],
        tier: str | None = None,
        max_tokens: int = 1024,
        prefer: list[str] | None = None,
        prefer_models: list[str] | None = None,
        avoid: list[str] | None = None,
        avoid_models: list[str] | None = None,
        max_failovers: int = 6,
        tools: list | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
        strengths: list[str] | None = None,
    ) -> BankResponse:
        if tier is None and not prefer_models:
            raise BankError("chat() requires either tier or prefer_models")
        tried: list[tuple[str, str]] = []
        avoid_set = set(avoid or [])
        avoid_models_set = set(avoid_models or [])
        attempts = 0
        for _ in range(max_failovers):
            attempts += 1
            lease = self.acquire(
                tier=tier,
                prefer=prefer,
                prefer_models=prefer_models,
                avoid=avoid_set,
                avoid_models=avoid_models_set,
                min_tokens=max_tokens,
                requires_tools=(tools is not None),
                requires_json=(response_format is not None),
                require_strengths=strengths,
            )
            if lease is None:
                raise BankExhausted(
                    f"no eligible (provider, model) for tier={tier}; tried={tried}"
                )
            try:
                resp = self._call(
                    lease, messages, max_tokens,
                    tools=tools, tool_choice=tool_choice,
                    response_format=response_format,
                )
                resp.meta["tier"] = tier
                resp.meta["attempts"] = attempts
                return resp
            except _Retryable429 as e:
                cooldown = e.retry_after if e.retry_after else 60
                self.state.set_cooldown(
                    lease.provider.name, lease.state_key, time.time() + cooldown, on_429=True
                )
                tried.append((lease.provider.name, lease.model.model_id))
                # Failover: avoid this (provider, model) for the rest of this chat() call.
                avoid_models_set = avoid_models_set | {lease.model.model_id}
                if lease.provider.shared_bucket:
                    avoid_set = avoid_set | {lease.provider.name}
            except _RetryableTransient as e:
                self.state.set_cooldown(
                    lease.provider.name, lease.state_key, time.time() + 30, on_429=False
                )
                tried.append((lease.provider.name, lease.model.model_id))
                avoid_models_set = avoid_models_set | {lease.model.model_id}
            except _PermanentDisable as e:
                self.state.disable_model(lease.provider.name, lease.model.model_id, e.reason)
                tried.append((lease.provider.name, lease.model.model_id))
        raise BankExhausted(f"max_failovers={max_failovers} exceeded; tried={tried}")

    def _call(
        self,
        lease: Lease,
        messages: list[dict],
        max_tokens: int,
        tools: list | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
    ) -> BankResponse:
        p, m = lease.provider, lease.model
        key = os.getenv(p.key_env)
        if not key:
            raise _PermanentDisable(f"env var {p.key_env} not set")
        url = p.base_url + "/chat/completions"
        payload: dict = {"model": m.model_id, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        t0 = time.perf_counter()
        try:
            r = self.client.post(url, json=payload, headers=headers, timeout=p.request_timeout)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
            latency = int((time.perf_counter() - t0) * 1000)
            self.state.log_event(p.name, m.model_id, 0, latency, f"{type(e).__name__}: {e}")
            raise _RetryableTransient(str(e))
        latency = int((time.perf_counter() - t0) * 1000)
        try:
            body = r.json()
        except Exception:
            body = r.text
        resp_headers = dict(r.headers)
        # update state from headers (works on all status codes — providers expose
        # remaining quota even on 429)
        self._reconcile_state(
            p, m, lease.state_key, resp_headers,
            ok=(r.status_code == 200),
            latency_ms=latency if r.status_code == 200 else None,
        )
        if r.status_code == 200:
            self.state.log_event(p.name, m.model_id, 200, latency, None)
            text = None
            tool_calls = None
            usage: dict = {}
            if isinstance(body, dict):
                try:
                    msg = body["choices"][0]["message"]
                    # gpt-oss-style models put visible answer in reasoning_content
                    text = msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning")
                    tool_calls = _normalize_tool_calls(msg)
                except Exception:
                    pass
                u = body.get("usage") or {}
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if k in u:
                        usage[k] = u[k]
            cur = self.state.get_state(p.name, lease.state_key)
            meta: dict = dict(usage)
            for fld in ("rpm_remaining", "rpd_remaining", "tpm_remaining", "tpd_remaining"):
                v = getattr(cur, fld, None)
                if v is not None:
                    meta[fld] = v
            return BankResponse(
                body=body,
                provider=p.name,
                model=m.model_id,
                latency_ms=latency,
                status=200,
                headers=resp_headers,
                text=text,
                tool_calls=tool_calls,
                meta=meta,
            )
        err_summary = json.dumps(body)[:300] if isinstance(body, dict) else str(body)[:300]
        self.state.log_event(p.name, m.model_id, r.status_code, latency, err_summary)
        if r.status_code == 429:
            ra = resp_headers.get("retry-after")
            try:
                ra_s = float(ra) if ra is not None else None
            except ValueError:
                ra_s = None
            raise _Retryable429(retry_after=ra_s)
        if r.status_code == 402:
            # Spend cap or paid-only. Long cooldown rather than disable, in case
            # the user tops up.
            raise _Retryable429(retry_after=86400)
        if r.status_code == 400:
            err_lower = err_summary.lower()
            if tools and "tool" in err_lower:
                self.state.mark_no_tools(p.name, m.model_id)
                raise _RetryableTransient(f"400: tools not supported by {m.model_id}")
            if response_format and ("response_format" in err_lower or "json_object" in err_lower or "json_schema" in err_lower or "json mode" in err_lower):
                self.state.mark_no_json(p.name, m.model_id)
                raise _RetryableTransient(f"400: json mode not supported by {m.model_id}")
            raise _PermanentDisable(f"400: {err_summary}")
        if r.status_code in (404, 410):
            raise _PermanentDisable(f"{r.status_code}: {err_summary}")
        if r.status_code in (401, 403):
            self.state.disable_provider(p.name, f"{r.status_code}: {err_summary}")
            raise _PermanentDisable(f"{r.status_code}: auth")
        if 500 <= r.status_code < 600:
            raise _RetryableTransient(f"{r.status_code}: {err_summary}")
        raise _PermanentDisable(f"unhandled {r.status_code}: {err_summary}")

    def _reconcile_state(
        self,
        p: Provider,
        m: Model,
        state_key: str,
        headers: dict,
        ok: bool,
        latency_ms: int | None = None,
    ):
        parser = PARSERS.get(p.name, lambda h: {})
        # case-insensitive lookup
        h_lower = {k.lower(): v for k, v in headers.items()}
        updates = parser(h_lower)
        cur = self.state.get_state(p.name, state_key)
        for k, v in updates.items():
            if v is not None:
                setattr(cur, k, v)
        if ok:
            cur.last_success_at = time.time()
            if latency_ms is not None:
                p50 = cur.latency_p50_ms
                cur.latency_p50_ms = (
                    float(latency_ms) if p50 is None else 0.8 * p50 + 0.2 * latency_ms
                )
        self.state.set_state(cur)

    # ---- streaming ----

    def stream(
        self,
        messages: list[dict],
        tier: str | None = None,
        max_tokens: int = 1024,
        prefer: list[str] | None = None,
        prefer_models: list[str] | None = None,
        avoid: list[str] | None = None,
        avoid_models: list[str] | None = None,
        max_failovers: int = 4,
        tools: list | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
        strengths: list[str] | None = None,
    ):
        """Yield text deltas as they arrive (OpenAI-compat SSE).

        Final yield is a dict with keys {provider, model, latency_ms, meta}.
        Failover only happens before the first byte; mid-stream errors raise.
        """
        if tier is None and not prefer_models:
            raise BankError("stream() requires either tier or prefer_models")
        tried: list[tuple[str, str]] = []
        avoid_set = set(avoid or [])
        avoid_models_set = set(avoid_models or [])
        attempts = 0
        for _ in range(max_failovers):
            attempts += 1
            lease = self.acquire(
                tier=tier,
                prefer=prefer,
                prefer_models=prefer_models,
                avoid=avoid_set,
                avoid_models=avoid_models_set,
                min_tokens=max_tokens,
                requires_tools=(tools is not None),
                requires_json=(response_format is not None),
                require_strengths=strengths,
            )
            if lease is None:
                raise BankExhausted(
                    f"no eligible (provider, model) for stream tier={tier}; tried={tried}"
                )
            try:
                gen = self._stream_call(
                    lease, messages, max_tokens,
                    tools=tools, tool_choice=tool_choice,
                    response_format=response_format,
                )
                # First send to driver — if connection-time error fires, failover.
                try:
                    first = next(gen)
                except StopIteration:
                    yield {
                        "provider": lease.provider.name,
                        "model": lease.model.model_id,
                        "latency_ms": 0,
                        "meta": {"tier": tier, "attempts": attempts},
                    }
                    return
                yield first
                yield from gen
                return
            except _Retryable429 as e:
                cooldown = e.retry_after if e.retry_after else 60
                self.state.set_cooldown(
                    lease.provider.name, lease.state_key, time.time() + cooldown, on_429=True
                )
                tried.append((lease.provider.name, lease.model.model_id))
                avoid_models_set = avoid_models_set | {lease.model.model_id}
                if lease.provider.shared_bucket:
                    avoid_set = avoid_set | {lease.provider.name}
            except _RetryableTransient as e:
                self.state.set_cooldown(
                    lease.provider.name, lease.state_key, time.time() + 30, on_429=False
                )
                tried.append((lease.provider.name, lease.model.model_id))
                avoid_models_set = avoid_models_set | {lease.model.model_id}
            except _PermanentDisable as e:
                self.state.disable_model(lease.provider.name, lease.model.model_id, e.reason)
                tried.append((lease.provider.name, lease.model.model_id))
        raise BankExhausted(f"stream max_failovers={max_failovers} exceeded; tried={tried}")

    def _stream_call(
        self,
        lease: Lease,
        messages: list[dict],
        max_tokens: int,
        tools: list | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
    ):
        p, m = lease.provider, lease.model
        key = os.getenv(p.key_env)
        if not key:
            raise _PermanentDisable(f"env var {p.key_env} not set")
        url = p.base_url + "/chat/completions"
        payload: dict = {
            "model": m.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        t0 = time.perf_counter()
        try:
            ctx = self.client.stream(
                "POST", url, json=payload, headers=headers, timeout=p.request_timeout
            )
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            raise _RetryableTransient(f"connect: {e}")
        with ctx as r:
            ttfb_ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                body = r.read().decode(errors="replace")
                self.state.log_event(p.name, m.model_id, r.status_code, ttfb_ms, body[:200])
                self._reconcile_state(p, m, lease.state_key, dict(r.headers), ok=False)
                if r.status_code == 429:
                    ra = r.headers.get("retry-after")
                    try:
                        ra_s = float(ra) if ra is not None else None
                    except ValueError:
                        ra_s = None
                    raise _Retryable429(retry_after=ra_s)
                if r.status_code == 402:
                    raise _Retryable429(retry_after=86400)
                if r.status_code == 400:
                    body_lower = body[:400].lower()
                    if tools and "tool" in body_lower:
                        self.state.mark_no_tools(p.name, m.model_id)
                        raise _RetryableTransient(f"400: tools not supported by {m.model_id}")
                    if response_format and ("response_format" in body_lower or "json_object" in body_lower or "json_schema" in body_lower or "json mode" in body_lower):
                        self.state.mark_no_json(p.name, m.model_id)
                        raise _RetryableTransient(f"400: json mode not supported by {m.model_id}")
                    raise _PermanentDisable(f"400: {body[:200]}")
                if r.status_code in (404, 410):
                    raise _PermanentDisable(f"{r.status_code}: {body[:200]}")
                if r.status_code in (401, 403):
                    self.state.disable_provider(p.name, f"{r.status_code}: auth")
                    raise _PermanentDisable(f"{r.status_code}: auth")
                if 500 <= r.status_code < 600:
                    raise _RetryableTransient(f"{r.status_code}: {body[:200]}")
                raise _PermanentDisable(f"unhandled {r.status_code}: {body[:200]}")

            # Stream OK — use ttfb_ms as latency sample for p50 EWMA
            self._reconcile_state(p, m, lease.state_key, dict(r.headers), ok=True, latency_ms=ttfb_ms)
            usage: dict = {}
            tool_buf: dict[int, dict] = {}
            try:
                for line in r.iter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        try:
                            d = chunk["choices"][0]["delta"]
                            piece = d.get("content") or d.get("reasoning_content")
                            if piece:
                                yield piece
                            for tc in d.get("tool_calls") or []:
                                if not isinstance(tc, dict):
                                    continue
                                idx = tc.get("index", 0)
                                slot = tool_buf.setdefault(
                                    idx,
                                    {"id": None, "function": {"name": None, "arguments": ""}},
                                )
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["function"]["name"] = fn["name"]
                                if fn.get("arguments"):
                                    slot["function"]["arguments"] += fn["arguments"]
                        except (KeyError, IndexError, TypeError):
                            pass
                        if isinstance(chunk, dict) and "usage" in chunk and chunk["usage"]:
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                                if k in chunk["usage"]:
                                    usage[k] = chunk["usage"][k]
            except (httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                # Mid-stream timeout — can't failover safely (caller may have
                # already consumed text). Bubble up.
                raise BankError(f"mid-stream error from {p.name}/{m.model_id}: {e}")
            latency = int((time.perf_counter() - t0) * 1000)
            self.state.log_event(p.name, m.model_id, 200, latency, None)
            cur = self.state.get_state(p.name, lease.state_key)
            meta: dict = dict(usage)
            for fld in ("rpm_remaining", "rpd_remaining", "tpm_remaining", "tpd_remaining"):
                v = getattr(cur, fld, None)
                if v is not None:
                    meta[fld] = v
            tool_calls = None
            if tool_buf:
                synthetic = {"tool_calls": [
                    {"id": v["id"], "type": "function", "function": v["function"]}
                    for _, v in sorted(tool_buf.items())
                ]}
                tool_calls = _normalize_tool_calls(synthetic)
            yield {
                "provider": p.name,
                "model": m.model_id,
                "latency_ms": latency,
                "ttfb_ms": ttfb_ms,
                "meta": meta,
                "tool_calls": tool_calls,
            }

    # ---- catalog discovery ----

    def discover_live_models(
        self,
        only: list[str] | None = None,
    ) -> dict[str, dict]:
        """Refresh provider catalogs from `/v1/models` (or `/models`).

        Returns dict[provider_name -> {live_ids, missing, new}]:
          - live_ids:  ids returned by provider right now
          - missing:   yaml-listed ids no longer in catalog (auto-disabled in state DB)
          - new:       ids in catalog but not in yaml (logged, NOT auto-added)

        Does not mutate providers.yaml — yaml is the human-curated source. To
        add `new` ids, edit yaml manually.
        """
        report: dict[str, dict] = {}
        targets = list(self.providers.values())
        if only:
            targets = [p for p in targets if p.name in only]
        for p in targets:
            key = os.getenv(p.key_env)
            if not key:
                report[p.name] = {"error": f"{p.key_env} not set"}
                continue
            url = p.base_url + "/models"
            try:
                r = self.client.get(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=30,
                )
            except Exception as e:
                report[p.name] = {"error": f"{type(e).__name__}: {e}"}
                continue
            if r.status_code != 200:
                report[p.name] = {"error": f"{r.status_code}", "body": str(r.text)[:200]}
                continue
            try:
                payload = r.json()
            except Exception as e:
                report[p.name] = {"error": f"json parse: {e}"}
                continue
            data = payload.get("data", payload) if isinstance(payload, dict) else payload
            raw_ids = {m["id"] for m in data if isinstance(m, dict) and m.get("id")}
            # Normalize: some providers return "models/<id>" or "<vendor>/<id>"
            # in /models but accept the bare suffix in /chat/completions.
            live_set = set(raw_ids)
            for rid in list(raw_ids):
                if "/" in rid:
                    live_set.add(rid.split("/", 1)[1])  # add suffix-only form
            live_ids = sorted(raw_ids)
            yaml_ids = {m.model_id for m in p.models}
            missing = sorted(mid for mid in yaml_ids if mid not in live_set)
            new = sorted(set(raw_ids) - yaml_ids - set(p.disabled_models))
            for mid in missing:
                self.state.disable_model(p.name, mid, "missing from /models on discovery")
            report[p.name] = {
                "live_ids": live_ids,
                "missing": missing,
                "new": new,
            }
        return report

    def close(self):
        self.client.close()


# ---------- CLI smoke test ----------

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="Bank smoke-test CLI")
    parser.add_argument("--tier", default="T3", choices=["T1", "T2", "T3", "any"])
    parser.add_argument("--prompt", default="reply with: pong")
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--prefer", nargs="*", default=None)
    parser.add_argument("--prefer-models", nargs="*", default=None)
    parser.add_argument("--avoid", nargs="*", default=None)
    parser.add_argument("--avoid-models", nargs="*", default=None)
    parser.add_argument(
        "--yaml",
        default=str(Path(__file__).parent / "scripts" / "providers.yaml"),
    )
    parser.add_argument(
        "--state",
        default=str(Path(__file__).parent / "bank-state.sqlite"),
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="refresh provider catalogs from /v1/models, then exit",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="stream tokens (SSE) instead of returning full response",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print per-provider/model health snapshot, then exit",
    )
    args = parser.parse_args()

    bank = Bank.from_yaml(args.yaml, state=args.state)
    if args.status:
        import time as _time
        now = _time.time()
        COL = "{:<12} {:<45} {:^4} {:^8} {:^7} {:^8} {:<25} {}"
        print(COL.format("PROVIDER", "MODEL", "TIER", "STATUS", "COOL(s)", "P50_MS", "STRENGTHS", "QUOTA"))
        print("-" * 130)
        for pname, prov in bank.providers.items():
            pdis = bank.state.is_provider_disabled(pname)
            for m in prov.models:
                key = bank._state_key(prov, m)
                s = bank.state.get_state(pname, key)
                doc = bank.state.get_doc_limits(pname, m.model_id)
                mdis = bank.state.is_model_disabled(pname, m.model_id)
                if pdis:
                    status = "PROV-DIS"
                elif mdis:
                    status = "DISABLED"
                elif s.cooldown_until > now:
                    status = "COOLDOWN"
                else:
                    status = "ok"
                cool = f"{s.cooldown_until - now:.0f}" if s.cooldown_until > now else "-"
                p50 = f"{s.latency_p50_ms:.0f}" if s.latency_p50_ms is not None else "-"
                parts = []
                for rem_attr, lim_attr, doc_key, label in [
                    ("tpm_remaining", "tpm_limit", "doc_tpm", "tpm"),
                    ("rpd_remaining", "rpd_limit", "doc_rpd", "rpd"),
                    ("rpm_remaining", "rpm_limit", "doc_rpm", "rpm"),
                    ("tpd_remaining", "tpd_limit", "doc_tpd", "tpd"),
                ]:
                    rem = getattr(s, rem_attr)
                    live_lim = getattr(s, lim_attr)
                    # use doc limit only as placeholder when no live data exists
                    lim = live_lim if rem is not None else (live_lim or doc.get(doc_key))
                    if rem is None and lim is None:
                        continue
                    rem_str = str(rem) if rem is not None else "-"
                    lim_str = f"/{lim}" if lim is not None else ""
                    parts.append(f"{rem_str}{lim_str}{label}")
                quota = "  ".join(parts) if parts else "-"
                strengths = ",".join(m.strengths) if m.strengths else "-"
                print(COL.format(pname[:12], m.model_id[:45], m.tier, status, cool, p50, strengths, quota))
        bank.close()
        return 0
    if args.discover:
        try:
            report = bank.discover_live_models()
            for name, info in report.items():
                if "error" in info:
                    print(f"[{name}] ERROR: {info['error']}")
                    continue
                missing = info.get("missing", [])
                new = info.get("new", [])
                print(f"[{name}] live={len(info['live_ids'])} missing={len(missing)} new={len(new)}")
                for mid in missing:
                    print(f"    -- DISABLED missing: {mid}")
                for mid in new[:20]:
                    print(f"    ++ catalog has new: {mid}")
                if len(new) > 20:
                    print(f"    ++ ... and {len(new)-20} more")
        finally:
            bank.close()
        return 0
    try:
        if args.stream:
            stream = bank.stream(
                messages=[{"role": "user", "content": args.prompt}],
                tier=None if args.tier == "any" else args.tier,
                max_tokens=args.max_tokens,
                prefer=args.prefer,
                prefer_models=args.prefer_models,
                avoid=args.avoid,
                avoid_models=args.avoid_models,
            )
            final = None
            for chunk in stream:
                if isinstance(chunk, str):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                else:
                    final = chunk
            print()
            if final:
                print(
                    f"\n[{final['provider']}/{final['model']}] "
                    f"{final['latency_ms']}ms (ttfb={final.get('ttfb_ms','-')}ms)"
                )
                if final.get("meta"):
                    print("meta:", json.dumps(final["meta"], indent=2))
        else:
            resp = bank.chat(
                messages=[{"role": "user", "content": args.prompt}],
                tier=None if args.tier == "any" else args.tier,
                max_tokens=args.max_tokens,
                prefer=args.prefer,
                prefer_models=args.prefer_models,
                avoid=args.avoid,
                avoid_models=args.avoid_models,
            )
            print(f"[{resp.provider}/{resp.model}] {resp.latency_ms}ms")
            print("text:", resp.text)
            if resp.meta:
                print("meta:", json.dumps(resp.meta, indent=2))
    except BankExhausted as e:
        print(f"EXHAUSTED: {e}", file=sys.stderr)
        return 2
    except BankError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        bank.close()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
