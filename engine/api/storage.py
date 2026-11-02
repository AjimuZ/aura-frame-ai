"""Library storage: where each library keeps generated data, and the path-safety checks around it."""

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.utils import apply_data_root

DATA_ROOT_ENV_VAR = "AURAFRAME_DATA_ROOT"
PHOTOS_ROOT_ENV_VAR = "AURAFRAME_PHOTOS_ROOT"
EXPORTS_ROOT_ENV_VAR = "AURAFRAME_EXPORTS_ROOT"
DEFAULT_DATA_ROOT = "/data"
DEFAULT_PHOTOS_ROOT = "/photos"
DEFAULT_EXPORTS_ROOT = "/exports"

# Library ids come from the URL. They start with an alphanumeric character and then allow only
# letters, digits, "_" and "-", so an id can never contain a separator or a dot segment.
LIBRARY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
# Photo ids are minted by the engine (photo_id_for): "IMG_" plus ten upper-case hex digits.
PHOTO_ID_PATTERN = r"^IMG_[0-9A-F]{10}$"

# Directories of one library that hold generated data only; the DELETE route removes these.
GENERATED_DIRS = ("working", "output", "reports")
# The part of GENERATED_DIRS that lives under the exports root, in the folder the person opens.
EXPORTED_DIRS = ("output", "reports")

IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


# Returns the command prefix that starts the real engine CLI with the running interpreter.
def _default_engine_command() -> tuple[str, ...]:
    return (sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"))


@dataclass(frozen=True)
class ApiSettings:
    """Where the API reads photos and keeps generated data, and how it starts the engine.

    `photos_root` is the read-only mount of the person's folder. `data_root` is the writable
    volume. `exports_root` is the writable mount of the folder the person opens; the copies and
    reports of library `<id>` go under `<exports_root>/<id>`. The three are stored resolved, and
    none may contain another, so removing generated data can never reach an original and an
    export can never land in the photos folder.

    `engine_command` is the fixed argument prefix of the engine child process. The run route
    appends its own validated arguments; no request ever supplies any part of the command.
    Tests replace it with a stub script.
    """

    data_root: Path
    photos_root: Path
    exports_root: Path
    engine_command: tuple[str, ...] = field(default_factory=_default_engine_command)

    def __post_init__(self) -> None:
        data = self.data_root.resolve()
        photos = self.photos_root.resolve()
        exports = self.exports_root.resolve()
        roots = {"data": data, "photos": photos, "exports": exports}
        for first, one in roots.items():
            for second, other in roots.items():
                if first < second and (one.is_relative_to(other) or other.is_relative_to(one)):
                    raise ValueError(
                        f"the {first} root and the {second} root must not contain each other"
                    )
        object.__setattr__(self, "data_root", data)
        object.__setattr__(self, "photos_root", photos)
        object.__setattr__(self, "exports_root", exports)


def load_settings_from_env() -> ApiSettings:
    """Build the settings from the environment, with the container defaults.

    Raises ValueError when two of the roots overlap.
    """
    return ApiSettings(
        data_root=Path(os.environ.get(DATA_ROOT_ENV_VAR) or DEFAULT_DATA_ROOT),
        photos_root=Path(os.environ.get(PHOTOS_ROOT_ENV_VAR) or DEFAULT_PHOTOS_ROOT),
        exports_root=Path(os.environ.get(EXPORTS_ROOT_ENV_VAR) or DEFAULT_EXPORTS_ROOT),
    )


def is_inside(path: Path, root: Path) -> bool:
    """Return True when `path`, after resolving symlinks and dot segments, lies under `root`."""
    return path.resolve().is_relative_to(root.resolve())


def library_dir(settings: ApiSettings, library_id: str) -> Path:
    """Return the directory of one library: `<data_root>/libraries/<id>`.

    Nothing is created. Raises ValueError when the id does not match the library id shape,
    which is the second line of defence after the route-level validation.
    """
    if not _is_library_id(library_id):
        raise ValueError("invalid library id")
    return settings.data_root / "libraries" / library_id


def library_exports_dir(settings: ApiSettings, library_id: str) -> Path:
    """Return the exports directory of one library: `<exports_root>/<id>`, holding `output` and `reports`.

    Nothing is created. Raises ValueError when the id does not match the library id shape. On
    the host this is the folder behind the `/exports` mount, so Electron main mirrors the layout.
    """
    if not _is_library_id(library_id):
        raise ValueError("invalid library id")
    return settings.exports_root / library_id


def library_config(settings: ApiSettings, library_id: str) -> dict[str, Any]:
    """Return an engine config dict with the library's generated paths.

    It reuses `apply_data_root`, so the layout (`working/auraframe.db`, `working/thumbs`) is the one
    the engine itself uses with `--data-root`, under the library directory. `output` and `reports`
    sit under the library's exports directory, as with `--exports-root`.
    """
    return apply_data_root(
        {"output": {}},
        str(library_dir(settings, library_id)),
        str(library_exports_dir(settings, library_id)),
    )


def library_db_file(settings: ApiSettings, library_id: str) -> Path:
    """Return the SQLite file of a library without creating it or its directories."""
    return Path(library_config(settings, library_id)["output"]["db_path"])


def library_control_file(settings: ApiSettings, library_id: str) -> Path:
    """Return the pause/cancel file the engine polls (`working/control.json`), without creating it."""
    return Path(library_config(settings, library_id)["output"]["working_dir"]) / "control.json"


def library_thumbs_dir(settings: ApiSettings, library_id: str) -> Path:
    """Return the thumbnail directory of a library without creating it."""
    return Path(library_config(settings, library_id)["output"]["thumbs_dir"])


def clear_library_data(settings: ApiSettings, library_id: str) -> int:
    """Delete the generated data of one library and return how many directories were removed.

    Only `working`, `output` and `reports` under `<data_root>/libraries/<id>` and `output` and
    `reports` under `<exports_root>/<id>` are touched. The first two of those are leftovers of
    exports written before the exports mount was connected. A directory that resolves outside its
    base (a planted symlink) is skipped instead of followed, and a symlink itself is unlinked,
    never traversed. Originals are never in scope.

    Raises OSError when a directory cannot be removed, for example an exports folder the API user
    may not write to; the route turns that into a structured 409.
    """
    removed = _remove_dirs(library_dir(settings, library_id), GENERATED_DIRS)
    removed += _remove_dirs(library_exports_dir(settings, library_id), EXPORTED_DIRS)
    return removed


# Removes the named subdirectories of `base` without following symlinks and returns the count.
def _remove_dirs(base: Path, names: tuple[str, ...]) -> int:
    removed = 0
    for name in names:
        target = base / name
        if target.is_symlink():
            target.unlink()
            removed += 1
        elif target.is_dir() and is_inside(target, base):
            shutil.rmtree(target)
            removed += 1
    return removed


# Library id shape check shared by the storage helpers.
def _is_library_id(value: str) -> bool:
    return re.fullmatch(LIBRARY_ID_PATTERN, value) is not None
