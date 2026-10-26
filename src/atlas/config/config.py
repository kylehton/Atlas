import re
from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    model_base_url: str = "http://localhost:8081"
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_admin_user_id: int | None = Field(default=None, gt=0)
    telegram_login_client_id: str | None = Field(default=None, min_length=1, max_length=64)
    telegram_login_client_secret: SecretStr | None = None
    settings_login_request_ttl_minutes: int = Field(default=10, ge=1, le=60)
    settings_session_ttl_hours: int = Field(default=2, ge=1, le=24)
    google_client_id: str | None = Field(default=None, min_length=1, max_length=256)
    google_client_secret: SecretStr | None = None
    credential_encryption_key: SecretStr | None = None
    google_oauth_request_ttl_minutes: int = Field(default=10, ge=1, le=30)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ATLAS_",
        extra="ignore",
    )

    @field_validator(
        "telegram_bot_token",
        "telegram_webhook_secret",
        "telegram_login_client_secret",
        "google_client_secret",
        "credential_encryption_key",
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

    @field_validator(
        "telegram_login_client_id",
        "telegram_admin_user_id",
        "google_client_id",
        mode="before",
    )
    @classmethod
    def validate_optional_identifier(cls, value: object) -> object:
        """Treat an empty optional identifier as unconfigured."""

        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("credential_encryption_key")
    @classmethod
    def validate_credential_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Require the URL-safe 32-byte key format used by Fernet."""

        if value is None:
            return None
        try:
            Fernet(value.get_secret_value().encode("ascii"))
        except (UnicodeEncodeError, ValueError):
            raise ValueError("credential_encryption_key must be a valid Fernet key") from None
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

    @model_validator(mode="after")
    def validate_telegram_webhook_secret(self) -> "Settings":
        """Match Telegram's allowed webhook-secret characters and length."""

        if self.telegram_webhook_secret is None:
            return self
        secret = self.telegram_webhook_secret.get_secret_value()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret) is None:
            raise ValueError("telegram_webhook_secret contains unsupported characters")
        return self

    @model_validator(mode="after")
    def validate_browser_login(self) -> "Settings":
        """Require complete Telegram OIDC credentials and HTTPS in production."""

        has_client_id = self.telegram_login_client_id is not None
        has_client_secret = self.telegram_login_client_secret is not None
        if has_client_id != has_client_secret:
            raise ValueError(
                "telegram_login_client_id and telegram_login_client_secret must be set together"
            )
        if self.environment == "production" and self.public_base_url.scheme != "https":
            raise ValueError("production public_base_url must use HTTPS")
        return self

    @model_validator(mode="after")
    def validate_google_oauth(self) -> "Settings":
        """Require a complete Google OAuth configuration before enabling connections."""

        has_client_id = self.google_client_id is not None
        has_client_secret = self.google_client_secret is not None
        if has_client_id != has_client_secret:
            raise ValueError("google_client_id and google_client_secret must be set together")
        if has_client_id and self.credential_encryption_key is None:
            raise ValueError("credential_encryption_key is required for Google OAuth")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
