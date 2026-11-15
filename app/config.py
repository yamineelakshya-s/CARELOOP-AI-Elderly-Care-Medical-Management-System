from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_ONLY_SESSION_SECRET = "development-only-change-this-secret"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "CareLoop AI"
    app_version: str = "1.0.0"
    debug: bool = True
    environment: str = "development"
    session_secret: str = DEVELOPMENT_ONLY_SESSION_SECRET
    database_path: str = "data/careloop.sqlite3"
    enable_demo_accounts: bool = False
    gemini_api_key: SecretStr | None = None
    brevo_api_key: SecretStr | None = None
    sender_email: str | None = None
    tick_secret: SecretStr | None = None
    app_base_url: str = "http://localhost:8000"
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        pattern=r"^[A-Za-z0-9._-]+$"
    )

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def empty_gemini_key_is_unconfigured(cls, value):
        return None if value == "" else value

    @field_validator("brevo_api_key", "tick_secret", mode="before")
    @classmethod
    def empty_secret_is_unconfigured(cls, value):
        return None if value == "" else value

    @field_validator("sender_email", mode="before")
    @classmethod
    def empty_sender_is_unconfigured(cls, value):
        return None if value == "" else value

    @field_validator("app_base_url")
    @classmethod
    def normalize_app_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("database_path", mode="after")
    @classmethod
    def resolve_database_path(cls, value: str) -> str:
        if value == ":memory:":
            return value
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())

    @model_validator(mode="after")
    def require_production_session_secret(self):
        if self.environment.lower() == "production" and (
            self.session_secret == DEVELOPMENT_ONLY_SESSION_SECRET
            or len(self.session_secret) < 32
        ):
            raise ValueError(
                "Production requires a unique session secret of at least 32 characters."
            )
        if self.environment.lower() == "production" and (
            self.tick_secret is None
            or len(self.tick_secret.get_secret_value()) < 32
        ):
            raise ValueError(
                "Production requires a unique tick secret of at least 32 characters."
            )
        return self


settings = Settings()
