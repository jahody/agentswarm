"""Shared fixtures for bank tests.

Tests run against a fresh SQLite-on-tmp_path StateStore. No real network —
network tests inject `httpx.MockTransport` via Bank's `client` parameter.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

# Make `bank.py` importable from a tests/ subdir.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bank as bank_mod  # noqa: E402


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "test-state.sqlite"


@pytest.fixture
def state(state_path):
    return bank_mod.StateStore(state_path)


@pytest.fixture
def empty_bank(state):
    """Bank with zero providers — useful for testing pure helpers."""
    return bank_mod.Bank(providers=[], state=state)


def make_provider(
    name="testprov",
    base_url="https://api.test.local/v1",
    key_env="TEST_KEY",
    shared_bucket=False,
    models=None,
    disabled_models=None,
    quota_strategy="headers",
):
    return bank_mod.Provider(
        name=name,
        base_url=base_url,
        key_env=key_env,
        quota_endpoint=None,
        quota_strategy=quota_strategy,
        shared_bucket=shared_bucket,
        request_timeout=10.0,
        data_used_for_training=False,
        models=models or [],
        disabled_models=disabled_models or {},
    )


def make_model(provider, model_id, tier="T2", **kw):
    return bank_mod.Model(provider=provider, model_id=model_id, tier=tier, **kw)


@pytest.fixture
def factory():
    """Factory helpers returned as a small object for tests to import once."""
    class F:
        provider = staticmethod(make_provider)
        model = staticmethod(make_model)
        Provider = bank_mod.Provider
        Model = bank_mod.Model
        LiveState = bank_mod.LiveState
        Bank = bank_mod.Bank
        StateStore = bank_mod.StateStore
        bank_mod = bank_mod
    return F


def make_mock_client(handler):
    """httpx.Client backed by a MockTransport calling `handler(request)`."""
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def mock_client_factory():
    return make_mock_client
