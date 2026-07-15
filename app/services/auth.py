from __future__ import annotations

from functools import lru_cache

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
    if algorithm == LEGACY_JWT_ALGORITHM:
        return decode_legacy_jwt(token, settings)
    return decode_jwks_jwt(token, settings)


def decode_jwks_jwt(token: str, settings: Settings) -> dict[str, object]:
    issuer = get_supabase_issuer(settings)
    client = get_jwks_client(get_supabase_jwks_url(settings))
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = decode(
            token,
            key=signing_key.key,
            algorithms=list(ASYMMETRIC_JWT_ALGORITHMS),
            issuer=issuer,
            options={"require": ["exp", "iss", "sub"]},
        )
    except (InvalidTokenError, PyJWKClientError) as exc:
        raise unauthorized("Invalid Supabase access token.") from exc
    if not isinstance(payload, dict):
        raise unauthorized("Invalid JWT payload shape.")
    return payload


def decode_legacy_jwt(token: str, settings: Settings) -> dict[str, object]:
    secret = settings.supabase_legacy_jwt_secret.strip()
    if not secret:
        raise unauthorized("Legacy Supabase session detected. Please sign in again.")
    try:
        payload = decode(
            token,
            key=secret,
            algorithms=[LEGACY_JWT_ALGORITHM],
            issuer=get_supabase_issuer(settings),
            options={"require": ["exp", "iss", "sub"]},
        )
    except InvalidTokenError as exc:
        raise unauthorized("Invalid Supabase access token.") from exc
    if not isinstance(payload, dict):
        raise unauthorized("Invalid JWT payload shape.")
    return payload
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


def get_token_algorithm(token: str) -> str:
    try:
        algorithm = str(get_unverified_header(token).get("alg", "")).strip()
    except InvalidTokenError as exc:
        raise unauthorized("Malformed JWT header.") from exc
    if algorithm not in ASYMMETRIC_JWT_ALGORITHMS | {LEGACY_JWT_ALGORITHM}:
        raise unauthorized("Unsupported Supabase token algorithm.")
    return algorithm


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def server_error(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
