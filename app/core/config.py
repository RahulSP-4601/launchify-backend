from functools import lru_cache
import logging
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
    log_level: str = "INFO"

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
    ffprobe_binary: str = "ffprobe"
    ffmpeg_timeout_seconds: int = 30
    tesseract_binary: str = "/opt/homebrew/bin/tesseract"
    tesseract_timeout_seconds: int = 15
    visual_analysis_concurrency: int = 1
    visual_analysis_frames_per_scene: int = 4
    visual_analysis_frame_width: int = 720
    visual_analysis_jpeg_quality: int = 10
    visual_analysis_scene_timeout_seconds: int = 25
    visual_analysis_total_budget_seconds: int = 120
    render_worker_dir: str = str(Path(__file__).resolve().parents[2] / "render-worker")
    render_timeout_seconds: int = 420
    preview_render_mode: Literal["proxy", "styled"] = "proxy"
    low_memory_final_mode: Literal["proxy", "render"] = "render"
    low_memory_final_width: int = 854
    low_memory_final_height: int = 480
    low_memory_final_fps: int = 20
    low_memory_final_render_scale: float = 0.5
    render_concurrency: int = 1
    render_offthread_video_threads: int = 1
    render_media_cache_size_mb: int = 32
    render_offthread_video_cache_size_mb: int = 32
    render_retry_attempts: int = 0
    run_job_runner: bool | None = None
    job_runner_poll_interval_seconds: int = 3
    job_stale_claim_window_seconds: int = 120
    job_heartbeat_interval_seconds: int = 10
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

    @property
    def serves_api(self) -> bool:
        return self.process_role in {"web", "all"}

    @property
    def serves_worker(self) -> bool:
        return self.process_role in {"worker", "all"}

    @property
    def effective_job_stale_claim_window_seconds(self) -> int:
        # Never reclaim an active render job before a healthy long-running stage
        # has had enough time to finish or emit the next heartbeat.
        return max(
            self.job_stale_claim_window_seconds,
            self.render_timeout_seconds + (self.job_heartbeat_interval_seconds * 3),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging() -> None:
    level_name = get_settings().log_level.upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
