"""Photo indexing: discover images and delegate per-file ingestion, then mark duplicates.

Ingestion (hashing, EXIF orientation, thumbnails) lives in src.ingest. Originals are
opened read-only and never modified.
"""

from pathlib import Path
from typing import Any

import imagehash

from . import progress_events as events
from .ingest import ingest_image
from .utils import Photo, discover_images, resolve_path


# Indexes all supported images in the input folder into Photo records.
def index_photos(
    input_folder: Path,
    config: dict[str, Any],
    include_subfolders: bool,
    generate_thumbnails: bool = True,
) -> list[Photo]:
    extensions = config["input"]["supported_extensions"]
    files = discover_images(input_folder, extensions, include_subfolders)

    photos: list[Photo] = []
    total = len(files)
    events.progress("indexing", 0, total, f"Found {total} images")

    for i, path in enumerate(files, start=1):
        photo = ingest_image(path, config, generate_thumbnail=generate_thumbnails)
        if photo.status == "rejected" and photo.reject_reason == "unreadable_image":
            events.log(f"Skipping unreadable image {path.name}", level="warning")
        photos.append(photo)
        if i % 25 == 0 or i == total:
            events.progress("indexing", i, total, f"Indexed {i}/{total}")

    return photos


# Marks duplicates on two levels: exact file hash, then near-duplicate perceptual hash.
# Exact (byte-identical) copies are always a hard reject. Near-duplicates are, under the
# Local Minimal Gate (keep_near_duplicates=True), only flagged and kept so OpenAI can pick
# the best expression/vibe; in strict/legacy mode they are rejected as before. The first
# occurrence of each visual group is always kept as a normal candidate.
def mark_duplicates(
    photos: list[Photo], max_distance: int = 5, keep_near_duplicates: bool = True
) -> int:
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
            if keep_near_duplicates:
                photo.is_near_duplicate = True
                if "local_flag_possible_duplicate" not in photo.soft_flags:
                    photo.soft_flags.append("local_flag_possible_duplicate")
            else:
                photo.status = "rejected"
                photo.reject_reason = "duplicate_similar"
        else:
            kept.append(current)
    return duplicates


# Standalone entry: index only (no scoring).
def run_index(input_folder: str, config: dict[str, Any], include_subfolders: bool) -> list[Photo]:
    folder = resolve_path(input_folder)
    return index_photos(folder, config, include_subfolders)
