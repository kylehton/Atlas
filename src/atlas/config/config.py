from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    model_base_url: str = "http://localhost:8081"
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ATLAS_",
        extra="ignore",
    )

    @field_validator(
        "telegram_bot_token",
        "telegram_webhook_secret",
        "google_client_secret",
        mode="before",
    )
    @classmethod
    def validate_optional_secret(cls, value: object) -> object:
        """Normalize blank secrets and reject common placeholder values."""

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.lower() in {"changeme", "replace-me", "replace_me", "secret"}:
                raise ValueError("secret contains a placeholder value")
            return stripped
        return value

    @model_validator(mode="after")
    def validate_production_database(self) -> "Settings":
        """Require production to use a remote PostgreSQL database with a real password."""

        try:
            database = make_url(self.database_url)
        except Exception as error:
            raise ValueError("database_url must be a valid SQLAlchemy URL") from error

        if self.environment != "production":
            return self

        if not database.drivername.startswith("postgresql"):
            raise ValueError("production database_url must use PostgreSQL")
        if database.host in {None, "localhost", "127.0.0.1", "::1"}:
            raise ValueError("production database_url must use a remote database host")
        if database.password in {None, "", "atlas", "changeme"}:
            raise ValueError("production database_url must use a non-placeholder password")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
