"""Robust image ingestion: open (incl. HEIC), normalize EXIF orientation, extract clean
metadata, compute exact + perceptual hashes, and build a cached thumbnail.

Originals are opened read-only and never modified. Thumbnails are written only under the
configured thumbnail cache directory. The HEIF opener is registered in src.utils.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS

from .utils import Photo, file_sha256, photo_id_for, thumbs_dir


# Converts a POSIX timestamp into an ISO 8601 UTC string.
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# Extracts a small, privacy-safe subset of EXIF fields (no GPS, no identity data).
def _safe_exif(image: Image.Image) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        raw = image.getexif()
    except Exception:  # noqa: BLE001 - many images have no/broken EXIF
        return out
    wanted = {"DateTimeOriginal", "DateTime", "Make", "Model", "Orientation"}
    for tag_id, value in raw.items():
        name = TAGS.get(tag_id, str(tag_id))
        if name in wanted:
            out[name] = str(value)
    return out


# Builds a thumbnail keyed by file hash; skips regeneration if a valid one already exists.
def _make_thumbnail(image: Image.Image, file_hash: str, thumb_dir: Path, size: int) -> str:
    out_path = thumb_dir / f"{file_hash[:16]}.jpg"
    if out_path.exists() and out_path.stat().st_size > 0:
        return str(out_path)
    thumb = image.copy()
    thumb.thumbnail((size, size))
    thumb.convert("RGB").save(out_path, "JPEG", quality=82)
    return str(out_path)


# Ingests a single image into a fully-populated Photo (metadata only; no scoring yet).
def ingest_image(path: Path, config: dict[str, Any], generate_thumbnail: bool = True) -> Photo:
    stat = path.stat()
    photo = Photo(
        id=photo_id_for(path),
        file_path=str(path),
        file_name=path.name,
        modified_at=_iso(stat.st_mtime),
        created_at=_iso(stat.st_ctime),
    )
    try:
        photo.file_hash = file_sha256(path)
        thumb_dir = thumbs_dir(config)
        thumb_size = int(config["output"].get("thumbnail_size", 512))
        with Image.open(path) as image:
            image.load()
            # Normalize orientation so width/height/thumbnail match the visual image.
            oriented = ImageOps.exif_transpose(image)
            photo.exif = _safe_exif(image)
            photo.width, photo.height = oriented.size
            photo.perceptual_hash = str(imagehash.phash(oriented))
            if generate_thumbnail:
                photo.thumbnail_path = _make_thumbnail(
                    oriented, photo.file_hash, thumb_dir, thumb_size
                )
    except Exception:  # noqa: BLE001 - one bad file must not stop the batch
        photo.status = "rejected"
        photo.reject_reason = "unreadable_image"
    return photo
