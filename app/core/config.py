from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Respiratory Health Awareness API"
    app_env: str = "development"

    database_url: str = "sqlite:///./respiratory_health.db"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()