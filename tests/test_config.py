import json
from pathlib import Path

from pydantic import SecretStr

from flight_price_lab.config import Settings


def test_api_key_is_required_and_hidden(monkeypatch) -> None:
    monkeypatch.setenv("SEARCHAPI_KEY", "test-secret-key")

    settings = Settings(_env_file=None)

    assert isinstance(settings.searchapi_key, SecretStr)
    assert settings.searchapi_key.get_secret_value() == "test-secret-key"
    assert "test-secret-key" not in repr(settings)


def test_settings_ignores_database_urls_from_shared_environment(monkeypatch) -> None:
    monkeypatch.setenv("SEARCHAPI_KEY", "test-secret-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example/dev")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://example/test")

    assert Settings(_env_file=None).searchapi_key.get_secret_value() == (
        "test-secret-key"
    )


def test_provider_concurrency_defaults_and_can_be_configured(monkeypatch) -> None:
    monkeypatch.setenv("SEARCHAPI_KEY", "test-secret-key")
    monkeypatch.delenv("SEARCH_PROVIDER_CONCURRENCY", raising=False)

    assert Settings(_env_file=None).search_provider_concurrency == 4

    monkeypatch.setenv("SEARCH_PROVIDER_CONCURRENCY", "8")
    assert Settings(_env_file=None).search_provider_concurrency == 8


def test_vercel_services_preserve_fastapi_api_paths() -> None:
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))

    assert config["services"]["backend"]["entrypoint"] == "backend.main:app"
    assert config["rewrites"][0] == {
        "source": "/api/(.*)",
        "destination": {"service": "backend"},
    }
