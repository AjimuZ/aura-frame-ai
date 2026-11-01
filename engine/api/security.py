"""Request guard: per-session bearer token and Host allowlist, applied to every route."""

import hmac
import os
import re
from typing import Awaitable, Callable, Iterable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .models import ErrorResponse

TOKEN_ENV_VAR = "AURAFRAME_API_TOKEN"
ALLOWED_HOSTS_ENV_VAR = "AURAFRAME_ALLOWED_HOSTS"
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost")
MIN_TOKEN_LENGTH = 16

# A Host header is a hostname or IPv4 address with an optional numeric port. Anything else
# (userinfo, paths, spaces, IPv6 brackets) is refused as a foreign host.
_HOST_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?$")


def load_token_from_env() -> str:
    """Return the session token from the environment.

    Raises RuntimeError when it is missing, so the service never starts unauthenticated.
    """
    token = os.environ.get(TOKEN_ENV_VAR, "")
    if not token:
        raise RuntimeError(f"{TOKEN_ENV_VAR} is not set; refusing to start without a session token")
    return token


def load_allowed_hosts_from_env() -> tuple[str, ...]:
    """Return the Host allowlist: the comma-separated environment value, else loopback names."""
    raw = os.environ.get(ALLOWED_HOSTS_ENV_VAR, "")
    names = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    return names or DEFAULT_ALLOWED_HOSTS


def is_allowed_host(host_header: str | None, allowed_hosts: Iterable[str]) -> bool:
    """Return True only when the Host header names an allowed host, with any numeric port.

    A missing or malformed header is refused. Names are compared case-insensitively. The
    port is not pinned because the gateway port is a deployment choice; the hostname is
    what defeats DNS rebinding.
    """
    if not host_header:
        return False
    match = _HOST_PATTERN.match(host_header.strip())
    if match is None:
        return False
    port = match.group("port")
    if port is not None and int(port) > 65535:
        return False
    return match.group("name").lower() in {name.lower() for name in allowed_hosts}


def is_valid_token(authorization: str | None, expected_token: str) -> bool:
    """Return True when `Authorization: Bearer <token>` carries the expected token.

    The comparison is constant-time so response timing does not leak token prefixes.
    """
    if not authorization:
        return False
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip().encode("utf-8"), expected_token.encode("utf-8"))


# Builds the JSON refusal response; it carries no CORS header by design.
def _refusal(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(code=code, message=message)
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def install_request_guard(app: FastAPI, token: str, allowed_hosts: Iterable[str]) -> None:
    """Install the guard as middleware so it also covers unknown paths and OPTIONS.

    Order: the Host is checked first (403 `foreign_host`), then the token (401
    `invalid_token`). Requests that pass reach the routes untouched. No CORS middleware
    is installed anywhere, so a browser page on another origin cannot read responses.

    Raises ValueError when the token is shorter than MIN_TOKEN_LENGTH.
    """
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"the session token must be at least {MIN_TOKEN_LENGTH} characters")
    hosts = tuple(allowed_hosts)

    # Refuses a request with a foreign Host or without the session token before any route runs.
    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not is_allowed_host(request.headers.get("host"), hosts):
            return _refusal(403, "foreign_host", "The Host header is not allowed.")
        if not is_valid_token(request.headers.get("authorization"), token):
            return _refusal(401, "invalid_token", "A valid session token is required.")
        return await call_next(request)
