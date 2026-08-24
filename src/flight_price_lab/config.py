"""Application settings loaded from environment variables or a local .env file."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings; secrets are required and hidden from representations."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    searchapi_key: SecretStr = Field(alias="SEARCHAPI_KEY", repr=False)
