"""Per-provider header parsers — golden tests against representative
header dicts captured in `findings.md` / `providers/<name>.md`."""
from __future__ import annotations

import bank


def test_parse_groq():
    h = {
        "x-ratelimit-remaining-requests": "29",
        "x-ratelimit-remaining-tokens": "5800",
    }
    out = bank.parse_groq(h)
    assert out["rpm_remaining"] == 29
    assert out["tpm_remaining"] == 5800


def test_parse_cerebras():
    h = {
        "x-ratelimit-remaining-requests-minute": "9",
        "x-ratelimit-remaining-requests-day": "999",
        "x-ratelimit-remaining-tokens-minute": "55000",
        "x-ratelimit-remaining-tokens-day": "899000",
    }
    out = bank.parse_cerebras(h)
    assert out == {
        "rpm_remaining": 9,
        "rpd_remaining": 999,
        "tpm_remaining": 55000,
        "tpd_remaining": 899000,
    }


def test_parse_sambanova():
    out = bank.parse_sambanova({"x-ratelimit-remaining-requests-day": "12"})
    assert out == {"rpd_remaining": 12}


def test_parse_huggingface():
    out = bank.parse_huggingface({
        "x-ratelimit-remaining-requests": "100",
        "x-ratelimit-remaining-tokens": "999999",
    })
    assert out["rpm_remaining"] == 100
    assert out["tpm_remaining"] == 999999


def test_parse_openrouter():
    assert bank.parse_openrouter({"x-ratelimit-remaining": "55"}) == {"rpd_remaining": 55}


def test_parse_handles_missing_keys():
    # All parsers must tolerate empty dicts — return None for absent fields.
    assert bank.parse_groq({}) == {"rpm_remaining": None, "tpm_remaining": None}
    assert bank.parse_cerebras({}) == {
        "rpm_remaining": None, "rpd_remaining": None,
        "tpm_remaining": None, "tpd_remaining": None,
    }


def test_parse_handles_non_numeric():
    assert bank.parse_groq({"x-ratelimit-remaining-requests": "n/a"})["rpm_remaining"] is None


def test_opaque_providers_return_empty():
    assert bank.PARSERS["google_ai_studio"]({"any": "headers"}) == {}
    assert bank.PARSERS["nvidia_nim"]({"any": "headers"}) == {}
