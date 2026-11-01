"""Structured API errors: one exception type rendered as the shared ErrorResponse body."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .models import ErrorResponse


class ApiError(Exception):
    """A refusal a route raises on purpose, with a stable machine code and a short message.

    The message never carries a filesystem path: clients show it to the person.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# Renders an ApiError as the ErrorResponse JSON body, so every refusal has the same shape.
async def _handle_api_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ApiError)
    body = ErrorResponse(code=error.code, message=error.message)
    return JSONResponse(status_code=error.status_code, content=body.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    """Register the ApiError handler on `app`."""
    app.add_exception_handler(ApiError, _handle_api_error)
