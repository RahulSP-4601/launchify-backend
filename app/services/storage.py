from __future__ import annotations

import http.client
import mimetypes
from tempfile import NamedTemporaryFile
from pathlib import Path
from urllib import error, parse, request
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.models.projects import AssetRecord


def upload_video(user_id: str, project_id: str, filename: str, content_type: str, file_bytes: bytes) -> AssetRecord:
    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    safe_name = parse.quote(filename, safe=".-_")
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


def download_asset_to_file(storage_path: str) -> Path:
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
        with request.urlopen(download_request, timeout=120) as response:
            while chunk := response.read(1024 * 1024):
                temp_file.write(chunk)
    except error.HTTPError as exc:
        temp_file.close()
        Path(temp_file.name).unlink(missing_ok=True)
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase Storage download failed: {detail}") from exc
    temp_file.close()
    return Path(temp_file.name)


def upload_video_file(
    user_id: str,
    project_id: str,
    filename: str,
    content_type: str,
    source_path: Path,
) -> AssetRecord:
    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    safe_name = parse.quote(filename, safe=".-_")
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


def send_storage_upload(
    endpoint: str,
    content_type: str,
    content_length: int,
    source_path: Path | None = None,
    file_bytes: bytes | None = None,
) -> None:
    parsed = urlsplit(endpoint)
    connection = build_connection(parsed)
    try:
        connection.putrequest("POST", parsed.path)
        for header_name, header_value in upload_headers(content_type, content_length).items():
            connection.putheader(header_name, header_value)
        connection.endheaders()
        stream_request_body(connection, source_path, file_bytes)
        response = connection.getresponse()
        detail = response.read().decode("utf-8", errors="ignore")
        if response.status >= 400:
            raise RuntimeError(f"Supabase Storage upload failed: {detail}")
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


def stream_request_body(
    connection: http.client.HTTPConnection,
    source_path: Path | None,
    file_bytes: bytes | None,
) -> None:
    if file_bytes is not None:
        connection.send(file_bytes)
        return
    if source_path is None:
        raise RuntimeError("No upload source was provided.")
    with source_path.open("rb") as file_pointer:
        while chunk := file_pointer.read(1024 * 1024):
            connection.send(chunk)
