import pytest


@pytest.fixture(autouse=True)
def _legacy_fixed_stake(monkeypatch):
    """Existing tests exercise the fixed-$ / evidence sizing; the dynamic %-of-bank stake has its own tests."""
    monkeypatch.setenv("VECTIQ_STAKE_PCT", "0")
