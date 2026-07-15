from functools import lru_cache
from pathlib import Path

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
    supabase_legacy_jwt_secret: str = ""
    database_url: str = ""
    supabase_storage_bucket: str = "launchify-assets"
    deepgram_api_key: str = ""
    deepgram_tts_model: str = "aura-2-thalia-en"

    openai_api_key: str = ""
    openai_script_model: str = "gpt-4.1-mini"
    openai_vision_model: str = "gpt-4.1-mini"
    ffmpeg_binary: str = "ffmpeg"
    tesseract_binary: str = "/opt/homebrew/bin/tesseract"
    render_worker_dir: str = str(Path(__file__).resolve().parents[2] / "render-worker")
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
