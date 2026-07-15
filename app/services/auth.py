from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


def get_authenticated_user_id(request: Request) -> str:
    token = get_bearer_token(request)
    payload = decode_and_verify_jwt(token)
    user_id = str(payload.get("sub", "")).strip()
    if not user_id:
        raise unauthorized("Supabase token is missing the subject claim.")
    return user_id


def get_bearer_token(request: Request) -> str:
    header_value = request.headers.get("authorization", "").strip()
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized("Missing Bearer token.")
    return token


def decode_and_verify_jwt(token: str) -> dict[str, object]:
    signing_input, signature = split_token(token)
    verify_signature(signing_input, signature)
    payload = decode_payload(signing_input)
    validate_expiration(payload)
    return payload


def split_token(token: str) -> tuple[str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise unauthorized("Malformed JWT.")
    return ".".join(parts[:2]), parts[2]


def verify_signature(signing_input: str, signature: str) -> None:
    secret = get_settings().supabase_jwt_secret
    if not secret:
        raise unauthorized("Supabase JWT verification is not configured.")
    expected = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    actual = urlsafe_b64decode(signature)
    if not hmac.compare_digest(expected, actual):
        raise unauthorized("Invalid Supabase token signature.")


def decode_payload(signing_input: str) -> dict[str, object]:
    _, payload_segment = signing_input.split(".", maxsplit=1)
    try:
        payload = json.loads(urlsafe_b64decode(payload_segment).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise unauthorized("Invalid JWT payload.") from exc
    if not isinstance(payload, dict):
        raise unauthorized("Invalid JWT payload shape.")
    return payload


def validate_expiration(payload: dict[str, object]) -> None:
    expires_at = payload.get("exp")
    if isinstance(expires_at, int | float) and float(expires_at) <= time.time():
        raise unauthorized("Supabase token has expired.")


def urlsafe_b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(f"{segment}{padding}".encode("utf-8"))
    except (ValueError, binascii.Error) as exc:
        raise unauthorized("Invalid base64 token segment.") from exc


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
