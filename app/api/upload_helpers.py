from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import HTTPException, UploadFile, status
from typing import Literal

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_MB = 50


def require_upload_name(filename: str | None, fallback_filename: str | None) -> str:
    upload_name = (filename or fallback_filename or "").strip()
    if not upload_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required.")
    return upload_name


def detect_editor_media_kind(content_type: str | None, filename: str) -> Literal["audio", "video"]:
    mime_hint = (content_type or "").lower()
    name_hint = filename.lower()
    if mime_hint.startswith("audio/") or name_hint.endswith((".mp3", ".wav", ".m4a")):
        return "audio"
    if mime_hint.startswith("video/") or name_hint.endswith((".mp4", ".mov", ".webm")):
        return "video"
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only audio and video assets are supported right now.")


async def write_upload_to_temp_file(upload: UploadFile) -> Path:
    with NamedTemporaryFile(delete=False) as temp_file:
        total_bytes = 0
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                await upload.close()
                temp_file.close()
                os.unlink(temp_file.name)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Uploaded file must be {MAX_UPLOAD_MB} MB or smaller.",
                )
            temp_file.write(chunk)
    await upload.close()
    if total_bytes == 0:
        os.unlink(temp_file.name)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    return Path(temp_file.name)
