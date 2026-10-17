import pytest


@pytest.fixture(autouse=True)
def no_real_llm_calls(monkeypatch):
    """Tests never call the real Anthropic API, even if a developer has a key set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
