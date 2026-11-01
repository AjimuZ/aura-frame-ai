"""Shared Pydantic models for API requests and responses."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

# A key or photo id inside a review session: non-empty and short.
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=128)]
MAX_ENTRIES = 20000


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


class ReviewSession(ApiModel):
    """A manual review session in the camelCase shape the Angular bridge already uses.

    Keys and photo ids are bounded so one request cannot store an unbounded document.
    """

    slotAssignments: dict[ShortText, ShortText] = Field(max_length=MAX_ENTRIES)
    locks: dict[ShortText, StrictBool] = Field(max_length=MAX_ENTRIES)
    shortlist: list[ShortText] = Field(max_length=MAX_ENTRIES)


class ProviderStatus(ApiModel):
    """Availability of one AI provider, as reported without contacting it."""

    id: str
    label: str
    cloud: bool
    state: Literal["available", "not_configured", "disabled"]
    detail: str


class ProvidersResponse(ApiModel):
    """The provider list behind `GET /api/providers`."""

    providers: list[ProviderStatus]


class ClearDataResponse(ApiModel):
    """Result of clearing a library's generated data: how many directories were removed."""

    removed: int


class ErrorResponse(ApiModel):
    """Structured refusal or failure body: a stable machine code plus a short message."""

    code: str
    message: str
