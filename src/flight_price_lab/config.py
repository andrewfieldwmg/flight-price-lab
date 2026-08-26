"""Application settings loaded from environment variables or a local .env file."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings; secrets are required and hidden from representations."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    searchapi_key: SecretStr = Field(alias="SEARCHAPI_KEY", repr=False)
    search_provider_concurrency: int = Field(
        default=4, alias="SEARCH_PROVIDER_CONCURRENCY", ge=1, le=8
    )

    @field_validator("searchapi_key")
    @classmethod
    def require_searchapi_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("SEARCHAPI_KEY must not be empty")
        return value
