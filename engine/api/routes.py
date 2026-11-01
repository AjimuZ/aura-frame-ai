"""HTTP routes under /api: runs, events, exports, photos, media by photo ID, providers, data and review."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Path as PathParam, Query
from fastapi.responses import FileResponse, StreamingResponse

from src import db

from .errors import ApiError
from .exports import export_photos
from .models import (
    ClearDataResponse,
    ExportRequest,
    ExportResponse,
    ProvidersResponse,
    ProviderStatus,
    ReviewSession,
    RunRequest,
    RunStatus,
)
from .runs import RunSupervisor
from .storage import (
    IMAGE_MEDIA_TYPES,
    LIBRARY_ID_PATTERN,
    PHOTO_ID_PATTERN,
    ApiSettings,
    clear_library_data,
    is_inside,
    library_config,
    library_db_file,
    library_thumbs_dir,
)

LibraryId = Annotated[str, PathParam(pattern=LIBRARY_ID_PATTERN)]
PhotoId = Annotated[str, PathParam(pattern=PHOTO_ID_PATTERN)]

# Browsers must not keep photo bytes beyond the session, and must not guess another type.
_MEDIA_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


# Opens a library database only when it already exists, so a read never creates data.
def _open_existing(settings: ApiSettings, library_id: str) -> sqlite3.Connection | None:
    if not library_db_file(settings, library_id).is_file():
        return None
    return db.connect(library_config(settings, library_id))


# Returns the provider list. It reports configuration only and contacts no service: the
# provider contract and its health checks arrive with package P03.
def _provider_statuses() -> list[ProviderStatus]:
    return [
        ProviderStatus(
            id="local-scoring",
            label="Local scoring",
            cloud=False,
            state="available",
            detail="Runs inside the api container; no model server is needed.",
        ),
        ProviderStatus(
            id="llama-server",
            label="Local vision model",
            cloud=False,
            state="not_configured",
            detail="No local vision model is configured yet.",
        ),
        ProviderStatus(
            id="codex",
            label="Codex CLI",
            cloud=True,
            state="disabled",
            detail="Optional and off by default; it needs an explicit opt-in.",
        ),
    ]


def build_api_router(settings: ApiSettings, runs: RunSupervisor) -> APIRouter:
    """Build the /api router bound to `settings` and the run supervisor `runs`.

    No route takes a filesystem path: libraries and photos are addressed by validated IDs,
    and every file the media route returns is looked up in SQLite and then checked to lie
    inside the photos root (originals) or the library's thumbnail directory. The engine
    command and its arguments are built server-side; a run request carries options only.
    """
    router = APIRouter(prefix="/api")

    # Starts an engine run over the mounted photos root. Answers 202 with the initial status,
    # or 409 when a run is already active. An empty body means the default options.
    @router.post(
        "/libraries/{library_id}/runs",
        status_code=202,
        response_model=RunStatus,
        response_model_exclude_none=True,
    )
    def start_run(
        library_id: LibraryId, options: Annotated[RunRequest, Body()] = RunRequest()
    ) -> RunStatus:
        return runs.start(library_id, options)

    # Reports the latest run of the library, or an idle status when it never ran.
    @router.get(
        "/libraries/{library_id}/runs/current",
        response_model=RunStatus,
        response_model_exclude_none=True,
    )
    def get_run(library_id: LibraryId) -> RunStatus:
        return runs.status(library_id)

    # Pause, resume and cancel act on the active run through the engine's control file.
    @router.post(
        "/libraries/{library_id}/runs/current/pause",
        response_model=RunStatus,
        response_model_exclude_none=True,
    )
    def pause_run(library_id: LibraryId) -> RunStatus:
        return runs.pause(library_id)

    @router.post(
        "/libraries/{library_id}/runs/current/resume",
        response_model=RunStatus,
        response_model_exclude_none=True,
    )
    def resume_run(library_id: LibraryId) -> RunStatus:
        return runs.resume(library_id)

    @router.post(
        "/libraries/{library_id}/runs/current/cancel",
        response_model=RunStatus,
        response_model_exclude_none=True,
    )
    def cancel_run(library_id: LibraryId) -> RunStatus:
        return runs.cancel(library_id)

    # Streams the run's JSONL events as server-sent events. The stream replays what the run
    # already produced and ends when the run does. Proxies must not buffer or cache it.
    @router.get("/libraries/{library_id}/events")
    def get_events(library_id: LibraryId) -> StreamingResponse:
        return StreamingResponse(
            runs.stream(library_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # Copies the chosen photos into the library's output folder. Originals are only read.
    @router.post("/libraries/{library_id}/exports", response_model=ExportResponse)
    def post_export(library_id: LibraryId, request: ExportRequest) -> ExportResponse:
        return ExportResponse(exported=export_photos(settings, library_id, request.ids))

    # Lists the analyzed photos of a library in the camelCase shape of the bridge.
    # A library with no database yet has no photos; that is an empty list, not an error.
    @router.get("/libraries/{library_id}/photos", response_model=list[dict[str, Any]])
    def get_photos(library_id: LibraryId) -> list[dict[str, Any]]:
        conn = _open_existing(settings, library_id)
        if conn is None:
            return []
        with closing(conn):
            rows = conn.execute("SELECT data FROM photos ORDER BY id").fetchall()
        return [json.loads(row["data"]) for row in rows]

    # Serves one image by photo ID. `variant=thumbnail` reads the generated thumbnail and
    # `variant=original` reads the read-only original. The stored path is never trusted
    # blindly: it must resolve inside its expected root, else the file counts as unavailable.
    @router.get("/libraries/{library_id}/media/{photo_id}")
    def get_media(
        library_id: LibraryId,
        photo_id: PhotoId,
        variant: Annotated[Literal["thumbnail", "original"], Query()] = "thumbnail",
    ) -> FileResponse:
        conn = _open_existing(settings, library_id)
        row = None
        if conn is not None:
            with closing(conn):
                row = conn.execute(
                    "SELECT file_path, thumb_path FROM photos WHERE id = ?", (photo_id,)
                ).fetchone()
        if row is None:
            raise ApiError(404, "photo_not_found", "No photo has this ID in this library.")
        if variant == "original":
            stored, root = row["file_path"], settings.photos_root
        else:
            stored, root = row["thumb_path"], library_thumbs_dir(settings, library_id)
        media_type = IMAGE_MEDIA_TYPES.get(Path(stored or "").suffix.lower())
        if not stored or media_type is None:
            raise ApiError(404, "media_unavailable", "This image is not available.")
        path = Path(stored)
        if not is_inside(path, root) or not path.resolve().is_file():
            raise ApiError(404, "media_unavailable", "This image is not available.")
        return FileResponse(path.resolve(), media_type=media_type, headers=_MEDIA_HEADERS)

    # Reports which AI providers can be used. Static for now; see _provider_statuses.
    @router.get("/providers", response_model=ProvidersResponse)
    def get_providers() -> ProvidersResponse:
        return ProvidersResponse(providers=_provider_statuses())

    # Removes the generated data of one library (database, thumbnails, outputs, reports).
    # It is idempotent: a library with nothing to remove answers `removed: 0`.
    @router.delete("/libraries/{library_id}/data", response_model=ClearDataResponse)
    def delete_data(library_id: LibraryId) -> ClearDataResponse:
        return ClearDataResponse(removed=clear_library_data(settings, library_id))

    # Returns the latest saved review session, or JSON null when none was saved yet.
    @router.get("/libraries/{library_id}/review", response_model=ReviewSession | None)
    def get_review(library_id: LibraryId) -> dict[str, Any] | None:
        conn = _open_existing(settings, library_id)
        if conn is None:
            return None
        with closing(conn):
            return db.load_latest_review_session(conn)

    # Saves a review session and echoes it back. The body is validated and bounded by
    # ReviewSession; unknown fields are rejected with HTTP 422.
    @router.put("/libraries/{library_id}/review", response_model=ReviewSession)
    def put_review(library_id: LibraryId, session: ReviewSession) -> ReviewSession:
        with closing(db.connect(library_config(settings, library_id))) as conn:
            db.save_review_session(
                conn, session.slotAssignments, session.locks, session.shortlist
            )
        return session

    return router
