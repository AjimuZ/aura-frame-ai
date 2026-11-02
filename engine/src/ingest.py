"""Photo ingestion: one read-only decode per original yields the content-hash ID, an upright
working copy, a thumbnail, a perceptual hash and a minimal EXIF subset.

The original is hashed (a read-only byte stream) and decoded once. EXIF orientation is applied
exactly once, here, so the working copy and the thumbnail are upright and carry no orientation
tag; every later stage reads the working copy and never the original. Generated files are
written only under the configured working and thumbnail directories.
The HEIF opener is registered in src.utils.
"""

import copy
import hashlib
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image, ImageOps

from .utils import (
    Photo,
    ensure_dir,
    file_sha256,
    thumbnail_path_for,
    working_copies_dir,
    working_copy_path,
)

WORKING_COPY_SIZE = 1536
THUMBNAIL_SIZE = 512
_WORKING_JPEG_QUALITY = 95
_THUMBNAIL_JPEG_QUALITY = 82

# Pillow format names the pipeline accepts, whatever the file extension says. MPO is the JPEG
# variant many cameras write; HEIF covers .heic through the registered opener.
SUPPORTED_FORMATS = frozenset({"JPEG", "MPO", "PNG", "WEBP", "HEIF"})

# EXIF tag numbers. Only these three values are ever read; everything else, including GPS, is
# ignored, so it cannot reach the database or the UI.
_EXIF_MAKE = 0x010F
_EXIF_MODEL = 0x0110
_EXIF_IFD_POINTER = 0x8769
_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_VALUE_MAX_CHARS = 64


# Converts a POSIX timestamp into an ISO 8601 UTC string.
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# Cleans one EXIF value into a short printable string, or returns None when nothing is left.
def _clean_exif_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    text = "".join(ch for ch in str(value) if ch.isprintable()).strip()
    return text[:_EXIF_VALUE_MAX_CHARS] or None


# Keeps only the capture time, camera make and camera model from the EXIF block. GPS, the
# orientation, serial numbers, owner names and every other tag are dropped. Returns an empty dict
# when the image has no EXIF or it cannot be parsed.
def _minimal_exif(image: Image.Image) -> dict[str, str]:
    try:
        raw = image.getexif()
        found = {
            "DateTimeOriginal": raw.get_ifd(_EXIF_IFD_POINTER).get(_EXIF_DATETIME_ORIGINAL),
            "Make": raw.get(_EXIF_MAKE),
            "Model": raw.get(_EXIF_MODEL),
        }
    except Exception:  # noqa: BLE001 - many images have no/broken EXIF
        return {}
    cleaned = {key: _clean_exif_text(value) for key, value in found.items() if value is not None}
    return {key: text for key, text in cleaned.items() if text}


# Saves an image as JPEG through a temporary name, so a crash never leaves a truncated file
# that a later run would treat as a valid derived copy.
def _save_jpeg(image: Image.Image, target: Path, quality: int, **options: Any) -> None:
    ensure_dir(target.parent)
    partial = target.with_name(target.name + ".part")
    image.save(partial, "JPEG", quality=quality, **options)
    os.replace(partial, target)


# Returns a copy no larger than `size` on its long side. Smaller images are never upscaled.
def _fit_long_side(image: Image.Image, size: int) -> Image.Image:
    fitted = image.copy()
    fitted.thumbnail((size, size), Image.Resampling.LANCZOS)
    return fitted


# True when the derived files of an already ingested photo are still on disk.
def _derived_files_present(config: dict[str, Any], photo_id: str, need_thumbnail: bool) -> bool:
    paths = [working_copy_path(config, photo_id)]
    if need_thumbnail:
        paths.append(thumbnail_path_for(config, photo_id))
    return all(p.is_file() and p.stat().st_size > 0 for p in paths)


# Builds an ID for a file whose bytes cannot be read, so the failure can still be reported. The
# prefix keeps it from ever colliding with a content hash (which is plain hex).
def _unreadable_id(path: Path) -> str:
    return "unreadable-" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]


# Builds the rejected record of a byte-identical copy. Copies share the content hash, so the
# record gets its own path-based ID (never colliding with a content hash) and keeps the shared
# hash in `file_hash`. It has no working copy: it exists so the rejection stays visible and counted.
def duplicate_record(path: Path, original: Photo) -> Photo:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]
    return Photo(
        id="duplicate-" + digest,
        file_path=str(path),
        file_name=path.name,
        modified_at=original.modified_at,
        created_at=original.created_at,
        file_hash=original.file_hash,
        status="rejected",
        reject_reason="duplicate_exact",
    )


# Ingests a single image into a Photo (metadata only; no scoring yet).
#
# The ID is the SHA-256 of the file content. When `known` holds a stored record with that ID and
# its working copy (and thumbnail) still exist, the file is not decoded again: the stored record is
# returned with the new path, name and times, and `carried_over` set. A moved or renamed file
# therefore keeps its results. A file that cannot be read comes back rejected as
# "unreadable_image"; bytes that Pillow recognizes as an image of a format outside
# SUPPORTED_FORMATS come back rejected as "unsupported_format" without any derived file. The
# original is only ever opened for reading.
def ingest_image(
    path: Path,
    config: dict[str, Any],
    generate_thumbnail: bool = True,
    known: Mapping[str, Photo] | None = None,
) -> Photo:
    try:
        stat = path.stat()
        digest = file_sha256(path)
    except OSError:
        return Photo(
            id=_unreadable_id(path),
            file_path=str(path),
            file_name=path.name,
            status="rejected",
            reject_reason="unreadable_image",
        )

    prior = known.get(digest) if known else None
    if prior is not None and _derived_files_present(config, digest, generate_thumbnail):
        reused = copy.deepcopy(prior)
        reused.file_path = str(path)
        reused.file_name = path.name
        reused.modified_at = _iso(stat.st_mtime)
        reused.created_at = _iso(stat.st_ctime)
        reused.carried_over = True
        if generate_thumbnail:
            reused.thumbnail_path = str(thumbnail_path_for(config, digest))
        return reused

    photo = Photo(
        id=digest,
        file_path=str(path),
        file_name=path.name,
        modified_at=_iso(stat.st_mtime),
        created_at=_iso(stat.st_ctime),
        file_hash=digest,
    )
    output = config["output"]
    working_size = int(output.get("working_copy_size", WORKING_COPY_SIZE))
    thumb_size = int(output.get("thumbnail_size", THUMBNAIL_SIZE))
    try:
        ensure_dir(working_copies_dir(config))
        with Image.open(path) as image:
            if image.format not in SUPPORTED_FORMATS:
                # Recognized as an image, but not one the pipeline handles (GIF, BMP, TIFF, ...).
                photo.status = "rejected"
                photo.reject_reason = "unsupported_format"
                return photo
            image.load()
            photo.exif = _minimal_exif(image)
            # The only place orientation is applied; the copies below carry no orientation tag.
            upright = ImageOps.exif_transpose(image).convert("RGB")
        photo.width, photo.height = upright.size
        working = _fit_long_side(upright, working_size)
        photo.perceptual_hash = str(imagehash.phash(working))
        _save_jpeg(working, working_copy_path(config, digest), _WORKING_JPEG_QUALITY, subsampling=0)
        if generate_thumbnail:
            thumb = _fit_long_side(working, thumb_size)
            target = thumbnail_path_for(config, digest)
            _save_jpeg(thumb, target, _THUMBNAIL_JPEG_QUALITY)
            photo.thumbnail_path = str(target)
    except Exception:  # noqa: BLE001 - one bad file must not stop the batch
        photo.status = "rejected"
        photo.reject_reason = "unreadable_image"
    return photo
