from __future__ import annotations

from urllib import error

from app.services.script_writer import describe_transport_error

TRANSIENT_HTTP_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524})


def retryable_visual_transport(
    exc: BaseException,
    payload_retryable: bool,
) -> bool:
    if isinstance(exc, error.HTTPError):
        return exc.code in TRANSIENT_HTTP_CODES
    if isinstance(exc, (error.URLError, TimeoutError)):
        return True
    return payload_retryable


def visual_failure_detail(exc: Exception) -> str:
    if isinstance(exc, error.HTTPError):
        body = exc.read().decode("utf-8", errors="ignore").strip()
        return body or f"HTTP {exc.code}"
    if isinstance(exc, (error.URLError, TimeoutError)):
        return describe_transport_error(exc)
    return str(exc)


def retry_delay_seconds(
    attempt: int,
    reduced: bool,
) -> float:
    base = 0.8 if reduced else 1.2
    return round(base * attempt, 2)


def retryable_visual_payload_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return "invalid visual analysis JSON" in message or "invalid visual analysis payload shape" in message or "empty visual analysis response" in message


def visual_error_message(exc: Exception, reduced: bool) -> str:
    suffix = " after reduced retry" if reduced else ""
    return f"OpenAI visual analysis failed{suffix}: {visual_failure_detail(exc)}"


def retryable_visual_failure(exc: RuntimeError) -> bool:
    cause = exc.__cause__
    if isinstance(cause, RuntimeError):
        return retryable_visual_payload_error(cause)
    if cause is not None:
        return retryable_visual_transport(cause, False)
    return retryable_visual_payload_error(exc)
