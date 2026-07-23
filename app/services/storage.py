from __future__ import annotations

import hashlib
import http.client
import logging
import mimetypes
import re
import socket
import time
from tempfile import NamedTemporaryFile
from pathlib import Path
from urllib import error, parse, request
from urllib.parse import urlsplit
from typing import Callable, Literal

from app.core.config import get_settings
from app.models.projects import AssetRecord, RenderedVideoRecord

FILENAME_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
UploadHeartbeat = Callable[[], None]
logger = logging.getLogger(__name__)


def upload_video(user_id: str, project_id: str, filename: str, content_type: str, file_bytes: bytes) -> AssetRecord:
    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    safe_name = sanitize_storage_filename(filename)
    storage_path = f"users/{user_id}/projects/{project_id}/video/{safe_name}"
    send_storage_upload(
        endpoint=f"{settings.supabase_url}/storage/v1/object/{bucket}/{storage_path}",
        content_type=content_type or guess_content_type(filename),
        content_length=len(file_bytes),
        file_bytes=file_bytes,
    )
    return AssetRecord(
        filename=filename,
        content_type=content_type or guess_content_type(filename),
        size_bytes=len(file_bytes),
        storage_path=storage_path,
    )


def guess_content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def download_asset(storage_path: str) -> bytes:
    settings = get_settings()
    endpoint = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}/{storage_path}"
    download_request = request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        },
        method="GET",
    )
    try:
        with request.urlopen(download_request, timeout=120) as response:
            content = response.read()
            return bytes(content)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase Storage download failed: {detail}") from exc


def download_asset_to_file(storage_path: str, heartbeat: UploadHeartbeat | None = None) -> Path:
    settings = get_settings()
    endpoint = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}/{storage_path}"
    download_request = request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        },
        method="GET",
    )
    temp_file = NamedTemporaryFile(delete=False)
    try:
        logger.info("Downloading source asset from storage path %s.", storage_path)
        with request.urlopen(download_request, timeout=120) as response:
            while chunk := response.read(1024 * 1024):
                temp_file.write(chunk)
                if heartbeat is not None:
                    heartbeat()
    except error.URLError as exc:
        temp_file.close()
        Path(temp_file.name).unlink(missing_ok=True)
        raise RuntimeError(f"Supabase Storage download failed: {exc.reason}") from exc
    except TimeoutError as exc:
        temp_file.close()
        Path(temp_file.name).unlink(missing_ok=True)
        raise RuntimeError("Supabase Storage download timed out.") from exc
    except error.HTTPError as exc:
        temp_file.close()
        Path(temp_file.name).unlink(missing_ok=True)
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase Storage download failed: {detail}") from exc
    temp_file.close()
    logger.info("Downloaded source asset from storage path %s into %s.", storage_path, temp_file.name)
    return Path(temp_file.name)


def cached_asset_file(storage_path: str, heartbeat: UploadHeartbeat | None = None) -> Path:
    settings = get_settings()
    cache_dir = Path(settings.asset_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cleanup_stale_cached_assets(cache_dir, settings.asset_cache_ttl_seconds)
    cache_path = cache_dir / cached_asset_filename(storage_path)
    if cached_asset_is_fresh(cache_path, settings.asset_cache_ttl_seconds):
        return cache_path
    downloaded = download_asset_to_file(storage_path, heartbeat=heartbeat)
    try:
        downloaded.replace(cache_path)
    finally:
        downloaded.unlink(missing_ok=True)
    return cache_path


def upload_video_file(
    user_id: str,
    project_id: str,
    filename: str,
    content_type: str,
    source_path: Path,
) -> AssetRecord:
    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    safe_name = sanitize_storage_filename(filename)
    storage_path = f"users/{user_id}/projects/{project_id}/video/{safe_name}"
    resolved_type = content_type or guess_content_type(filename)
    send_storage_upload(
        endpoint=f"{settings.supabase_url}/storage/v1/object/{bucket}/{storage_path}",
        content_type=resolved_type,
        content_length=source_path.stat().st_size,
        source_path=source_path,
    )
    return AssetRecord(
        filename=filename,
        content_type=resolved_type,
        size_bytes=source_path.stat().st_size,
        storage_path=storage_path,
    )


def upload_rendered_video_file(
    user_id: str,
    project_id: str,
    variant: Literal["preview", "final"],
    filename: str,
    source_path: Path,
    duration_seconds: float,
    heartbeat: UploadHeartbeat | None = None,
) -> RenderedVideoRecord:
    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    safe_name = sanitize_storage_filename(filename)
    storage_path = f"users/{user_id}/projects/{project_id}/renders/{variant}/{safe_name}"
    send_storage_upload(
        endpoint=f"{settings.supabase_url}/storage/v1/object/{bucket}/{storage_path}",
        content_type="video/mp4",
        content_length=source_path.stat().st_size,
        source_path=source_path,
        heartbeat=heartbeat,
    )
    return RenderedVideoRecord(
        filename=filename,
        content_type="video/mp4",
        size_bytes=source_path.stat().st_size,
        storage_path=storage_path,
        duration_seconds=duration_seconds,
        variant=variant,
    )


def upload_audio_file(
    user_id: str,
    project_id: str,
    filename: str,
    source_path: Path,
) -> AssetRecord:
    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    safe_name = sanitize_storage_filename(filename)
    storage_path = f"users/{user_id}/projects/{project_id}/audio/{safe_name}"
    content_type = "audio/mpeg"
    send_storage_upload(
        endpoint=f"{settings.supabase_url}/storage/v1/object/{bucket}/{storage_path}",
        content_type=content_type,
        content_length=source_path.stat().st_size,
        source_path=source_path,
    )
    return AssetRecord(
        filename=filename,
        content_type=content_type,
        size_bytes=source_path.stat().st_size,
        storage_path=storage_path,
    )


def send_storage_upload(
    endpoint: str,
    content_type: str,
    content_length: int,
    source_path: Path | None = None,
    file_bytes: bytes | None = None,
    heartbeat: UploadHeartbeat | None = None,
) -> None:
    parsed = urlsplit(endpoint)
    connection = build_connection(parsed)
    try:
        connection.putrequest("POST", parsed.path)
        for header_name, header_value in upload_headers(content_type, content_length).items():
            connection.putheader(header_name, header_value)
        connection.endheaders()
        stream_request_body(connection, source_path, file_bytes, heartbeat=heartbeat)
        response = wait_for_upload_response(connection, heartbeat=heartbeat)
        detail = response.read().decode("utf-8", errors="ignore")
        if response.status >= 400:
            raise RuntimeError(f"Supabase Storage upload failed: {detail}")
        if heartbeat is not None:
            heartbeat()
    finally:
        connection.close()


def build_connection(parsed_url: parse.SplitResult) -> http.client.HTTPConnection:
    if not parsed_url.hostname:
        raise RuntimeError("Supabase Storage URL is missing a hostname.")
    port = parsed_url.port
    if parsed_url.scheme == "https":
        return http.client.HTTPSConnection(parsed_url.hostname, port, timeout=120)
    return http.client.HTTPConnection(parsed_url.hostname, port, timeout=120)


def upload_headers(content_type: str, content_length: int) -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
        "Content-Type": content_type,
        "Content-Length": str(content_length),
        "x-upsert": "true",
    }


def sanitize_storage_filename(filename: str) -> str:
    cleaned = filename.strip().replace("/", "-").replace("\\", "-")
    stem = Path(cleaned).stem or "upload"
    suffix = Path(cleaned).suffix
    safe_stem = FILENAME_SANITIZE_PATTERN.sub("-", stem).strip(".-_") or "upload"
    safe_suffix = FILENAME_SANITIZE_PATTERN.sub("", suffix)[:10]
    filename_digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    safe_name = f"{safe_stem}-{filename_digest}{safe_suffix}".lower()
    return parse.quote(safe_name, safe=".-_")


def cached_asset_filename(storage_path: str) -> str:
    parsed_path = parse.unquote(storage_path)
    suffix = Path(parsed_path).suffix[:10]
    digest = hashlib.sha1(parsed_path.encode("utf-8")).hexdigest()
    return f"{digest}{suffix}".lower()


def cached_asset_is_fresh(cache_path: Path, ttl_seconds: int) -> bool:
    if not cache_path.exists():
        return False
    if ttl_seconds <= 0:
        return True
    return (time.time() - cache_path.stat().st_mtime) <= ttl_seconds


def cleanup_stale_cached_assets(cache_dir: Path, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    cutoff = time.time() - ttl_seconds
    for candidate in cache_dir.iterdir():
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def stream_request_body(
    connection: http.client.HTTPConnection,
    source_path: Path | None,
    file_bytes: bytes | None,
    heartbeat: UploadHeartbeat | None = None,
) -> None:
    if file_bytes is not None:
        connection.send(file_bytes)
        if heartbeat is not None:
            heartbeat()
        return
    if source_path is None:
        raise RuntimeError("No upload source was provided.")
    with source_path.open("rb") as file_pointer:
        while chunk := file_pointer.read(1024 * 1024):
            connection.send(chunk)
            if heartbeat is not None:
                heartbeat()


def wait_for_upload_response(
    connection: http.client.HTTPConnection,
    *,
    heartbeat: UploadHeartbeat | None,
) -> http.client.HTTPResponse:
    settings = get_settings()
    deadline = time.monotonic() + settings.effective_job_stale_claim_window_seconds
    timeout_seconds = max(settings.job_heartbeat_interval_seconds, 1)
    sock = connection.sock
    if sock is None:
        return connection.getresponse()
    previous_timeout = sock.gettimeout()
    sock.settimeout(timeout_seconds)
    try:
        while True:
            try:
                return connection.getresponse()
            except socket.timeout as exc:
                if heartbeat is not None:
                    heartbeat()
                if time.monotonic() >= deadline:
                    raise RuntimeError("Supabase Storage upload response timed out.") from exc
    finally:
        sock.settimeout(previous_timeout)
