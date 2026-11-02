"""Export of the proposed final set as numbered copies: `001_<original name>`.

Originals are only read. Each copy goes into one generated folder that must never be a folder holding an original, and
a file is created only when nothing exists under that name, so no original can be overwritten.
"""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from . import progress_events as events

# The prefix is at least three digits and grows when the set has more than 999 photos.
MIN_DIGITS = 3
MAX_NAME_CHARS = 200
# Matches a file this stage wrote earlier; only such files are removed from the folder before a new export.
_EXPORTED = re.compile(r"^\d{3,}_")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]+")


@dataclass(frozen=True)
class ExportItem:
    """One photo to copy: its ID, its place in the final set (from 1) and the file name it gets."""

    photo_id: str
    number: int
    name: str


@dataclass
class ExportResult:
    """What was copied (photo ID to the new file name) and which photos had no readable source file."""

    copied: dict[str, str]
    missing: list[str]


# Returns the original name made safe as one path component: no separators or control characters, never empty, and
# no longer than MAX_NAME_CHARS (the extension is kept when the name is cut).
def safe_original_name(name: str) -> str:
    cleaned = _UNSAFE.sub("_", name).strip().strip(".") or "photo"
    if len(cleaned) <= MAX_NAME_CHARS:
        return cleaned
    suffix = Path(cleaned).suffix[:16]
    return cleaned[: MAX_NAME_CHARS - len(suffix)] + suffix


# Plans the export of `final_ids` in order: the number is the place in the set, zero-padded to the width of the last
# number (at least three digits), then an underscore and the original name. A photo with no known name is called
# "photo". The names are unique because the numbers are.
def plan_export(final_ids: Sequence[str], file_names: Mapping[str, str]) -> list[ExportItem]:
    width = max(MIN_DIGITS, len(str(len(final_ids))))
    return [
        ExportItem(photo_id, number, f"{number:0{width}d}_{safe_original_name(file_names.get(photo_id, ''))}")
        for number, photo_id in enumerate(final_ids, start=1)
    ]


# Removes the numbered files an earlier export left in the folder. Other files and links are never touched.
def _clear_earlier_export(folder: Path) -> None:
    for entry in folder.iterdir():
        if entry.is_file() and not entry.is_symlink() and _EXPORTED.match(entry.name):
            entry.unlink()


# Copies the planned photos from `sources` (photo ID to the original's path) into `dest_dir`, creating it if needed.
# Originals are opened for reading only. Raises ValueError, before anything is written, when `dest_dir` is a link or
# the folder of a source file, so a copy can never land beside or over an original. A source that is missing or
# unreadable is listed in `missing` and skipped; the other numbers are kept, so a gap shows what is absent.
def export_copies(plan: Sequence[ExportItem], sources: Mapping[str, str], dest_dir: Path) -> ExportResult:
    if dest_dir.is_symlink():
        raise ValueError("the export folder must not be a link")
    destination = dest_dir.resolve()
    for item in plan:
        source = sources.get(item.photo_id)
        if source and Path(source).resolve().parent == destination:
            raise ValueError("the export folder must not be a folder that holds an original")
    dest_dir.mkdir(parents=True, exist_ok=True)
    _clear_earlier_export(dest_dir)

    copied: dict[str, str] = {}
    missing: list[str] = []
    for item in plan:
        source = sources.get(item.photo_id)
        if not source or not Path(source).is_file():
            missing.append(item.photo_id)
            continue
        target = dest_dir / item.name
        created = False
        try:
            with open(source, "rb") as reader, open(target, "xb") as writer:
                created = True
                shutil.copyfileobj(reader, writer)
        except OSError:
            missing.append(item.photo_id)
            if created:
                target.unlink(missing_ok=True)  # only a partial copy that this call created
            continue
        shutil.copystat(source, target, follow_symlinks=False)
        copied[item.photo_id] = item.name
    events.log(f"Exported {len(copied)} copies; {len(missing)} had no readable source.")
    return ExportResult(copied, missing)
