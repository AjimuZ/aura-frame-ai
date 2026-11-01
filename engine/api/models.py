"""Shared Pydantic models for API requests and responses."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from .storage import PHOTO_ID_PATTERN

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


class RunRequest(ApiModel):
    """Options of one analysis run, in the camelCase of the bridge's `AnalysisConfig`.

    There is deliberately no folder or path field: a run always analyzes the mounted photos
    root, and a body that names one is refused as an unknown field.
    """

    runGemma: StrictBool = False
    preset: Literal["fast", "balanced", "quality"] = "balanced"
    includeSubfolders: StrictBool = True


class RunStatus(ApiModel):
    """Progress of the current or latest run, in the shape of the bridge's `AnalysisStatus`."""

    libraryId: str | None
    step: str
    running: bool
    paused: bool
    current: int
    total: int
    message: str
    startedAt: str | None = None
    finishedAt: str | None = None


PhotoIdText = Annotated[str, StringConstraints(pattern=PHOTO_ID_PATTERN)]
MAX_EXPORT_IDS = 500


class ExportRequest(ApiModel):
    """The photos to copy into the library's export folder, by photo ID only."""

    ids: list[PhotoIdText] = Field(min_length=1, max_length=MAX_EXPORT_IDS)


class ExportResponse(ApiModel):
    """How many photos were copied. The destination is not reported: it is a server path."""

    exported: int


class ErrorResponse(ApiModel):
    """Structured refusal or failure body: a stable machine code plus a short message."""

    code: str
    message: str
