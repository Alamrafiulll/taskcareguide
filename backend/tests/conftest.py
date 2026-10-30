import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def disable_runtime_ai_for_tests(monkeypatch):
    """Keep ordinary tests offline and deterministic regardless of developer .env."""
    monkeypatch.setenv("AI_PROVIDER", "none")
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
