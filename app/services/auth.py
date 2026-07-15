from __future__ import annotations

import json
from functools import lru_cache
from urllib import error, request

from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError, PyJWKClient, decode, get_unverified_header
from jwt.exceptions import PyJWKClientError

from app.core.config import Settings, get_settings

ASYMMETRIC_JWT_ALGORITHMS = {"ES256", "RS256"}
LEGACY_JWT_ALGORITHM = "HS256"


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
    settings = get_settings()
    algorithm = get_token_algorithm(token)
    try:
        if algorithm == LEGACY_JWT_ALGORITHM:
            return decode_legacy_jwt(token, settings)
        return decode_jwks_jwt(token, settings)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        return decode_with_supabase_user_lookup(token, settings)


def decode_jwks_jwt(token: str, settings: Settings) -> dict[str, object]:
    client = get_jwks_client(get_supabase_jwks_url(settings))
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = decode(
            token,
            key=signing_key.key,
            algorithms=list(ASYMMETRIC_JWT_ALGORITHMS),
            options={"require": ["exp", "sub"]},
        )
    except (InvalidTokenError, PyJWKClientError) as exc:
        raise unauthorized("Invalid Supabase access token.") from exc
    return validate_payload(payload, settings)


def decode_legacy_jwt(token: str, settings: Settings) -> dict[str, object]:
    secret = settings.supabase_legacy_jwt_secret.strip()
    if not secret:
        raise unauthorized("Legacy Supabase session detected. Please sign in again.")
    try:
        payload = decode(
            token,
            key=secret,
            algorithms=[LEGACY_JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except InvalidTokenError as exc:
        raise unauthorized("Invalid Supabase access token.") from exc
    return validate_payload(payload, settings)


@lru_cache(maxsize=1)
def get_jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


def get_supabase_issuer(settings: Settings) -> str:
    base_url = settings.supabase_url.strip().rstrip("/")
    if not base_url:
        raise server_error("SUPABASE_URL is required for JWT verification.")
    return f"{base_url}/auth/v1"


def get_supabase_jwks_url(settings: Settings) -> str:
    return f"{get_supabase_issuer(settings)}/.well-known/jwks.json"


def get_supabase_project_ref(settings: Settings) -> str:
    base_url = settings.supabase_url.strip()
    if not base_url:
        raise server_error("SUPABASE_URL is required for JWT verification.")
    hostname = base_url.removeprefix("https://").removeprefix("http://").split("/", 1)[0]
    project_ref, _, _ = hostname.partition(".")
    if not project_ref:
        raise server_error("SUPABASE_URL is missing the project reference.")
    return project_ref


def get_token_algorithm(token: str) -> str:
    try:
        algorithm = str(get_unverified_header(token).get("alg", "")).strip()
    except InvalidTokenError as exc:
        raise unauthorized("Malformed JWT header.") from exc
    if algorithm not in ASYMMETRIC_JWT_ALGORITHMS | {LEGACY_JWT_ALGORITHM}:
        raise unauthorized("Unsupported Supabase token algorithm.")
    return algorithm


def validate_payload(payload: object, settings: Settings) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise unauthorized("Invalid JWT payload shape.")
    issuer = str(payload.get("iss", "")).strip()
    if issuer and issuer not in {get_supabase_issuer(settings), "supabase"}:
        raise unauthorized("Invalid Supabase token issuer.")
    project_ref = str(payload.get("ref", "")).strip()
    if project_ref and project_ref != get_supabase_project_ref(settings):
        raise unauthorized("Invalid Supabase project reference.")
    return payload


def decode_with_supabase_user_lookup(token: str, settings: Settings) -> dict[str, object]:
    auth_request = request.Request(
        f"{get_supabase_issuer(settings)}/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": settings.supabase_anon_key,
        },
        method="GET",
    )
    try:
        with request.urlopen(auth_request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
            raise unauthorized("Invalid Supabase access token.") from exc
        raise server_error("Supabase auth verification is temporarily unavailable.") from exc
    except (error.URLError, json.JSONDecodeError) as exc:
        raise server_error("Supabase auth verification is temporarily unavailable.") from exc
    user_id = str(payload.get("id", "")).strip() if isinstance(payload, dict) else ""
    if not user_id:
        raise unauthorized("Supabase token is missing the subject claim.")
    return {"sub": user_id}


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def server_error(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
