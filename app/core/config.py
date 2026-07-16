from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "launchify-backend"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    process_role: Literal["web", "worker", "all"] = "web"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_legacy_jwt_secret: str = ""
    database_url: str = ""
    database_connect_timeout_seconds: int = 10
    database_pool_size: int = 8
    supabase_storage_bucket: str = "launchify-assets"
    deepgram_api_key: str = ""
    deepgram_tts_model: str = "aura-2-thalia-en"

    openai_api_key: str = ""
    openai_script_model: str = "gpt-4.1-mini"
    openai_vision_model: str = "gpt-4.1-mini"
    ffmpeg_binary: str = "ffmpeg"
    ffmpeg_timeout_seconds: int = 30
    tesseract_binary: str = "/opt/homebrew/bin/tesseract"
    tesseract_timeout_seconds: int = 15
    visual_analysis_concurrency: int = 1
    render_worker_dir: str = str(Path(__file__).resolve().parents[2] / "render-worker")
    render_timeout_seconds: int = 240
    run_job_runner: bool | None = None
    job_runner_poll_interval_seconds: int = 3
    transcription_warn_seconds: int = 45
    script_generation_warn_seconds: int = 25
    planning_warn_seconds: int = 45
    preview_render_warn_seconds: int = 60
    final_render_warn_seconds: int = 120
    total_pipeline_warn_seconds: int = 240
    trial_minutes_limit: int = 10
    posthog_api_key: str = ""
    sentry_dsn: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def should_run_job_runner(self) -> bool:
        if self.run_job_runner is not None:
            return self.run_job_runner
        return self.process_role in {"worker", "all"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
