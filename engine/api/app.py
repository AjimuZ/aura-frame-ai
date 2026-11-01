"""FastAPI application factory: builds the guarded app, the health route and the /api routes."""

from typing import Iterable

from fastapi import FastAPI

from . import API_VERSION
from .errors import install_error_handlers
from .models import HealthResponse
from .routes import build_api_router
from .security import (
    install_request_guard,
    load_allowed_hosts_from_env,
    load_token_from_env,
)
from .storage import ApiSettings, load_settings_from_env


def create_app(
    token: str, allowed_hosts: Iterable[str], settings: ApiSettings | None = None
) -> FastAPI:
    """Build the API application with the request guard installed.

    Every request, including unknown paths, needs the bearer `token` and an allowed Host.
    The interactive docs and the OpenAPI document are disabled to keep the surface small,
    and no CORS middleware is added: the only client is the Electron main process.

    `settings` names the data and photos roots; when omitted they come from the environment.
    Raises ValueError for a short token or overlapping roots.
    """
    app = FastAPI(
        title="AuraFrame AI engine API",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_request_guard(app, token, allowed_hosts)
    install_error_handlers(app)
    app.include_router(build_api_router(settings or load_settings_from_env()))

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
