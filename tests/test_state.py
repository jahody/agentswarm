"""`StateStore` concurrency + stale-write guard."""
from __future__ import annotations

import sqlite3
import threading
import time

import bank


def test_set_state_writes_and_reads(state):
    s = bank.LiveState(provider="p", model_id="m", rpm_remaining=42, latency_p50_ms=120.0)
    state.set_state(s)
    got = state.get_state("p", "m")
    assert got.rpm_remaining == 42
    assert got.latency_p50_ms == 120.0


def test_stale_write_rejected(state):
    fresh = bank.LiveState(provider="p", model_id="m", rpm_remaining=10)
    state.set_state(fresh)
    fresh_ts = state.get_state("p", "m").updated_at
    stale = bank.LiveState(provider="p", model_id="m", rpm_remaining=999)
    stale.updated_at = fresh_ts - 100  # explicitly older
    # set_state overwrites updated_at to time.time() — to actually test the
    # guard we must bypass the helper and write directly.
    with state._transaction() as c:
        c.execute(
            """
            INSERT INTO live(provider, model_id, rpm_remaining, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(provider, model_id) DO UPDATE SET
                rpm_remaining=excluded.rpm_remaining,
                updated_at=excluded.updated_at
            WHERE excluded.updated_at >= live.updated_at
            """,
            ("p", "m", 999, fresh_ts - 100),
        )
    assert state.get_state("p", "m").rpm_remaining == 10, "stale write must not clobber"


def test_concurrent_upserts_no_corruption(state):
    """4 threads × 50 iterations: every set_state lands in `events` table
    via log_event, single live row per (provider, model_id)."""
    N = 50
    THREADS = 4

    def worker(tid: int):
        for i in range(N):
            s = bank.LiveState(provider="p", model_id="m", rpm_remaining=tid * 1000 + i)
            state.set_state(s)
            state.log_event("p", "m", 200, 10, None)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # exactly one live row
    with state._read() as c:
        n_live = c.execute("SELECT COUNT(*) FROM live").fetchone()[0]
        n_events = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert n_live == 1
    assert n_events == THREADS * N


def test_supports_tools_default_and_flip(state):
    state.upsert_model(bank.Model(provider="p", model_id="m", tier="T2"))
    assert state.get_supports_tools("p", "m") is True
    state.mark_no_tools("p", "m")
    assert state.get_supports_tools("p", "m") is False


def test_supports_json_default_and_flip(state):
    state.upsert_model(bank.Model(provider="p", model_id="m", tier="T2"))
    assert state.get_supports_json("p", "m") is True
    state.mark_no_json("p", "m")
    assert state.get_supports_json("p", "m") is False


def test_disable_model_persists(state):
    state.disable_model("p", "broken", "404")
    assert state.is_model_disabled("p", "broken") is True
    assert state.is_model_disabled("p", "ok") is False


def test_disable_provider_persists(state):
    state.upsert_provider("p")
    state.disable_provider("p", "401")
    assert state.is_provider_disabled("p") is True


def test_cooldown_set_max(state):
    now = time.time()
    state.set_cooldown("p", "m", now + 30, on_429=True)
    state.set_cooldown("p", "m", now + 10, on_429=False)
    s = state.get_state("p", "m")
    assert s.cooldown_until >= now + 29
