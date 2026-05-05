"""`Bank._score` — selection ordering across tiers."""
from __future__ import annotations


def test_t3_picks_lowest_latency(empty_bank, factory):
    p = factory.provider(name="groq")
    m = factory.model("groq", "llama-3.1-8b-instant", tier="T3")
    fast = factory.LiveState(provider="groq", model_id=m.model_id, latency_p50_ms=120.0)
    slow = factory.LiveState(provider="groq", model_id=m.model_id, latency_p50_ms=900.0)
    s_fast = empty_bank._score(p, m, fast, None, None, "T3")
    s_slow = empty_bank._score(p, m, slow, None, None, "T3")
    assert s_fast > s_slow, "lower p50 must outrank higher p50 in T3"


def test_t3_unknown_latency_loses(empty_bank, factory):
    p = factory.provider(name="groq")
    m = factory.model("groq", "x", tier="T3")
    measured = factory.LiveState(provider="groq", model_id="x", latency_p50_ms=400.0)
    unknown = factory.LiveState(provider="groq", model_id="x", latency_p50_ms=None)
    s_meas = empty_bank._score(p, m, measured, None, None, "T3")
    s_unk = empty_bank._score(p, m, unknown, None, None, "T3")
    assert s_meas > s_unk


def test_t2_uses_yaml_priority(empty_bank, factory):
    # TIER_PRIORITY for T2: groq > huggingface > sambanova > google_ai_studio > nvidia_nim > openrouter
    p_groq = factory.provider(name="groq")
    p_or = factory.provider(name="openrouter")
    m = factory.model("x", "x", tier="T2")
    s = factory.LiveState(provider="x", model_id="x")
    assert empty_bank._score(p_groq, m, s, None, None, "T2") > empty_bank._score(
        p_or, m, s, None, None, "T2"
    )


def test_prefer_models_dominates_prefer_providers(empty_bank, factory):
    p = factory.provider(name="huggingface")  # not in prefer
    m_target = factory.model("huggingface", "wanted", tier="T2")
    m_other = factory.model("huggingface", "ignored", tier="T2")
    s = factory.LiveState(provider="huggingface", model_id="x")
    s_target = empty_bank._score(p, m_target, s, prefer=["groq"], prefer_models=["wanted"], tier="T2")
    s_other = empty_bank._score(p, m_other, s, prefer=["groq"], prefer_models=["wanted"], tier="T2")
    assert s_target > s_other


def test_prefer_providers_bumps_score(empty_bank, factory):
    p_pref = factory.provider(name="huggingface")
    p_norm = factory.provider(name="huggingface")
    m = factory.model("huggingface", "x", tier="T2")
    s = factory.LiveState(provider="huggingface", model_id="x")
    s_pref = empty_bank._score(p_pref, m, s, prefer=["huggingface"], prefer_models=None, tier="T2")
    s_norm = empty_bank._score(p_norm, m, s, prefer=None, prefer_models=None, tier="T2")
    assert s_pref > s_norm
