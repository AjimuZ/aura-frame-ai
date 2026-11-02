"""Photo indexing: discover images and delegate per-file ingestion, then mark duplicates.

Ingestion (hashing, EXIF orientation, working copies, thumbnails) lives in src.ingest.
Originals are opened read-only and never modified.
"""

from pathlib import Path
from typing import Any

import imagehash

from . import progress_events as events
from .ingest import duplicate_record, ingest_image
from .utils import Photo, discover_images, resolve_path


# Indexes all supported images in the input folder into Photo records, one per distinct content.
#
# Photo IDs are content hashes, so byte-identical files share one ID and would overwrite each
# other in the database. The policy is therefore: the first copy in path order is the photo, and
# every later byte-identical copy becomes a separate record rejected as "duplicate_exact" (see
# ingest.duplicate_record), so the rejection is recorded and counted rather than silent. `known`
# maps IDs of already analyzed photos to their stored records; a file whose content is in it is
# not decoded again and keeps its results even when it was moved or renamed.
def index_photos(
    input_folder: Path,
    config: dict[str, Any],
    include_subfolders: bool,
    generate_thumbnails: bool = True,
    known: dict[str, Photo] | None = None,
) -> list[Photo]:
    extensions = config["input"]["supported_extensions"]
    files = discover_images(input_folder, extensions, include_subfolders)

    photos: list[Photo] = []
    seen: dict[str, Photo] = dict(known or {})
    first_of_run: set[str] = set()
    collapsed = 0
    total = len(files)
    events.progress("indexing", 0, total, f"Found {total} images")

    for i, path in enumerate(files, start=1):
        photo = ingest_image(path, config, generate_thumbnail=generate_thumbnails, known=seen)
        if photo.id in first_of_run:
            collapsed += 1
            photos.append(duplicate_record(path, photo))
        else:
            first_of_run.add(photo.id)
            if photo.status == "rejected" and photo.reject_reason == "unreadable_image":
                events.log(f"Skipping unreadable image {path.name}", level="warning")
            elif photo.status == "rejected" and photo.reject_reason == "unsupported_format":
                events.log(f"Rejecting unsupported image format {path.name}", level="warning")
            else:
                # A later copy of these bytes finds the derived files and is not decoded again.
                seen[photo.id] = photo
            photos.append(photo)
        if i % 25 == 0 or i == total:
            events.progress("indexing", i, total, f"Indexed {i}/{total}")

    if collapsed:
        events.log(f"{collapsed} byte-identical copies rejected as exact duplicates.", "info")
    return photos


# Marks duplicates on two levels: exact file hash, then near-duplicate perceptual hash.
# Exact (byte-identical) copies are always rejected. Near-duplicates are only flagged and kept
# so the later grouping and comparison stages can pick the best of each group. The first
# occurrence of each visual group is always kept as a normal candidate.
def mark_duplicates(photos: list[Photo], max_distance: int = 5) -> int:
    duplicates = 0
    seen_hashes: set[str] = set()
    kept: list[imagehash.ImageHash] = []

    for photo in photos:
        if photo.status == "rejected":
            continue
        # Level 1: byte-identical files.
        if photo.file_hash:
            if photo.file_hash in seen_hashes:
                photo.status = "rejected"
                photo.reject_reason = "duplicate_exact"
                duplicates += 1
                continue
            seen_hashes.add(photo.file_hash)
        # Level 2: visually near-identical files (perceptual hash distance).
        if not photo.perceptual_hash:
            continue
        current = imagehash.hex_to_hash(photo.perceptual_hash)
        if any((current - existing) <= max_distance for existing in kept):
            duplicates += 1
            photo.is_near_duplicate = True
            if "local_flag_possible_duplicate" not in photo.soft_flags:
                photo.soft_flags.append("local_flag_possible_duplicate")
        else:
            kept.append(current)
    return duplicates


# Standalone entry: index only (no scoring).
def run_index(input_folder: str, config: dict[str, Any], include_subfolders: bool) -> list[Photo]:
    folder = resolve_path(input_folder)
    return index_photos(folder, config, include_subfolders)
