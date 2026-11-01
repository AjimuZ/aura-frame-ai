"""Shared Pydantic models for API requests and responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Base for every request and response body.

    Unknown fields are rejected so a malformed or hostile body fails validation (HTTP 422)
    instead of being silently ignored. Strings are stripped of surrounding whitespace.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HealthResponse(ApiModel):
    """Liveness answer: the service is up and which API version it speaks."""

    status: Literal["ok"]
    version: str


class ErrorResponse(ApiModel):
    """Structured refusal or failure body: a stable machine code plus a short message."""

    code: str
    message: str
