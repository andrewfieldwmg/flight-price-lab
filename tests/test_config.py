from pydantic import SecretStr

from flight_price_lab.config import Settings


def test_api_key_is_required_and_hidden(monkeypatch) -> None:
    monkeypatch.setenv("SEARCHAPI_KEY", "test-secret-key")

    settings = Settings(_env_file=None)

    assert isinstance(settings.searchapi_key, SecretStr)
    assert settings.searchapi_key.get_secret_value() == "test-secret-key"
    assert "test-secret-key" not in repr(settings)
