from typing import Literal

from pydantic import EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


MIN_JWT_SECRET_LENGTH = 32

PLACEHOLDER_JWT_SECRETS = {
    "change-me",
    "replace_with_a_secure_random_secret",
}


class Settings(BaseSettings):
    app_name: str = "Respiratory Health Awareness API"
    app_env: str = "development"

    database_url: str = "sqlite:///./respiratory_health.db"

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: EmailStr | None = None
    smtp_from_name: str = "RespiraCare"
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=30)
    frontend_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("smtp_from_email", mode="before")
    @classmethod
    def empty_sender_is_unconfigured(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(
        cls,
        value: str,
    ) -> str:
        if (
            value in PLACEHOLDER_JWT_SECRETS
            or len(value) < MIN_JWT_SECRET_LENGTH
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be set in .env to a "
                "random secret of at least "
                f"{MIN_JWT_SECRET_LENGTH} characters. "
                "Generate one with: python -c "
                '"import secrets; '
                'print(secrets.token_urlsafe(48))"'
            )

        return value


settings = Settings()
