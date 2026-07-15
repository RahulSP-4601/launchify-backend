from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "launchify-backend"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    database_url: str = ""
    supabase_storage_bucket: str = "launchify-assets"
    deepgram_api_key: str = ""

    openai_api_key: str = ""
    posthog_api_key: str = ""
    sentry_dsn: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
