"""HTTP routes under /api: photos, media by photo ID, providers, generated data and review."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path as PathParam, Query
from fastapi.responses import FileResponse

from src import db

from .errors import ApiError
from .models import ClearDataResponse, ProvidersResponse, ProviderStatus, ReviewSession
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


def build_api_router(settings: ApiSettings) -> APIRouter:
    """Build the /api router bound to `settings`.

    No route takes a filesystem path: libraries and photos are addressed by validated IDs,
    and every file the media route returns is looked up in SQLite and then checked to lie
    inside the photos root (originals) or the library's thumbnail directory.
    """
    router = APIRouter(prefix="/api")

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
