"""FastAPI application factory: builds the guarded app and the health route."""

from typing import Iterable

from fastapi import FastAPI

from . import API_VERSION
from .models import HealthResponse
from .security import (
    install_request_guard,
    load_allowed_hosts_from_env,
    load_token_from_env,
)


def create_app(token: str, allowed_hosts: Iterable[str]) -> FastAPI:
    """Build the API application with the request guard installed.

    Every request, including unknown paths, needs the bearer `token` and an allowed Host.
    The interactive docs and the OpenAPI document are disabled to keep the surface small,
    and no CORS middleware is added: the only client is the Electron main process.
    """
    app = FastAPI(
        title="AuraFrame AI engine API",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_request_guard(app, token, allowed_hosts)

    # Answers liveness only; it touches no photo, model or database.
    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=API_VERSION)

    return app


def create_app_from_env() -> FastAPI:
    """Build the app from the environment, for `uvicorn --factory api.app:create_app_from_env`.

    Raises RuntimeError when the session token variable is missing.
    """
    return create_app(load_token_from_env(), load_allowed_hosts_from_env())
