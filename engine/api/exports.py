"""Copy-only export of chosen photos into a library's output folder."""

import shutil
from contextlib import closing
from pathlib import Path

from src import db

from .errors import ApiError
from .storage import (
    IMAGE_MEDIA_TYPES,
    ApiSettings,
    is_inside,
    library_config,
    library_db_file,
    library_dir,
)

EXPORT_FOLDER = "top_06_final_set"


def export_photos(settings: ApiSettings, library_id: str, photo_ids: list[str]) -> int:
    """Copy the originals of `photo_ids` into `<library>/output/top_06_final_set` and return the count.

    The files are named `NN_<photo id><extension>` in request order, and repeated ids are copied
    once. Originals are only read: each source must be a regular image file inside the photos
    root, and each destination is unlinked before it is written so a planted symlink is never
    followed. Nothing is copied unless every id passes, so a refusal leaves the folder as it was.

    Raises ApiError 422 for an id the library does not know or a photo that is no longer
    available inside the photos root, and 409 when the output folder is not safe to write.
    """
    ordered = list(dict.fromkeys(photo_ids))
    sources = _resolve_sources(settings, library_id, ordered)
    base = library_dir(settings, library_id)
    config = library_config(settings, library_id)
    destination = Path(config["output"]["base_dir"]) / EXPORT_FOLDER
    if destination.is_symlink() or not is_inside(destination, base):
        raise ApiError(409, "export_folder_unsafe", "The export folder cannot be used.")
    destination.mkdir(parents=True, exist_ok=True)
    for position, (photo_id, source) in enumerate(zip(ordered, sources), start=1):
        target = destination / f"{position:02d}_{photo_id}{source.suffix.lower()}"
        target.unlink(missing_ok=True)
        shutil.copy2(source, target)
    return len(sources)


# Looks every id up in the library database and returns the resolved original of each, in order.
def _resolve_sources(settings: ApiSettings, library_id: str, ordered: list[str]) -> list[Path]:
    if not library_db_file(settings, library_id).is_file():
        raise ApiError(422, "unknown_photo_ids", "Some photos are not part of this library.")
    with closing(db.connect(library_config(settings, library_id))) as conn:
        marks = ",".join("?" for _ in ordered)
        rows = conn.execute(
            f"SELECT id, file_path FROM photos WHERE id IN ({marks})", ordered  # noqa: S608
        ).fetchall()
    stored = {row["id"]: row["file_path"] for row in rows}
    if len(stored) != len(ordered):
        raise ApiError(422, "unknown_photo_ids", "Some photos are not part of this library.")
    sources: list[Path] = []
    for photo_id in ordered:
        path = Path(stored[photo_id] or "")
        usable = (
            path.suffix.lower() in IMAGE_MEDIA_TYPES
            and is_inside(path, settings.photos_root)
            and path.resolve().is_file()
        )
        if not usable:
            raise ApiError(422, "photo_unavailable", "Some photos are no longer available.")
        sources.append(path.resolve())
    return sources
