"""Finds a local model directory and accepts it only when it holds verified safetensors weights.

Nothing here reaches the network. A model is a plain sub-directory of the models volume, named in
config.yaml, so a hub identifier or a path outside that volume can never be requested. Pickle-based
weights can run code when loaded, so a directory is accepted only through `.safetensors` files whose
first bytes form a valid safetensors header (the same check the model fetcher applies).

Standard library only, so the engine check imports it without the torch stack.
"""

import os
import re
from pathlib import Path
from typing import Any

from .model_fetch.fetch import PICKLE_SUFFIXES, FetchError, check_content_format
from .utils import resolve_path

MODELS_DIR_ENV_VAR = "AURAFRAME_MODELS_DIR"
WEIGHT_SUFFIX = ".safetensors"
_DIR_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_MODELS_GUIDE = "docs/operations.md#model-files"


class ModelUnavailable(Exception):
    """A model cannot be used, so its values stay unknown. The message names no host path.

    `code` is one of: not_configured, directory_missing, no_weights, unsafe_format, invalid_weights,
    dependency_missing, cuda_unavailable, load_failed. `model` is the config key (qrealign, siglip2).
    """

    def __init__(self, model: str, code: str, message: str) -> None:
        super().__init__(message)
        self.model = model
        self.code = code
        self.message = message


# Returns the models volume: AURAFRAME_MODELS_DIR when set (the api container), else models.dir from the
# configuration, resolved against the repository root.
def models_root(config: dict[str, Any]) -> Path:
    override = os.environ.get(MODELS_DIR_ENV_VAR, "").strip()
    return Path(override) if override else resolve_path(str(config.get("models", {}).get("dir", "./data/models")))


# Returns the configured sub-directory name of a model. It must be one plain name: a hub identifier such as
# "owner/name", an absolute path or a parent reference is refused, so no value can lead to a download or out of
# the models volume. Raises ModelUnavailable (not_configured) otherwise.
def model_dir_name(config: dict[str, Any], model: str) -> str:
    name = (config.get("models", {}).get(model) or {}).get("dir")
    if not isinstance(name, str) or not _DIR_NAME.fullmatch(name) or ".." in name:
        raise ModelUnavailable(
            model,
            "not_configured",
            f"models.{model}.dir must name one directory of the models volume (see {_MODELS_GUIDE})",
        )
    return name


# Returns the verified weight files (sorted) of a model directory. A directory holding only pickle-based
# files is refused as unsafe; a `.safetensors` file with a malformed header is refused as invalid. Pickle files
# next to safetensors weights are ignored, because the loaders are told to read safetensors only.
# Raises ModelUnavailable (directory_missing, no_weights, unsafe_format or invalid_weights).
def verify_model_dir(directory: Path, model: str) -> list[Path]:
    label = directory.name
    if not directory.is_dir():
        raise ModelUnavailable(
            model, "directory_missing", f"model directory '{label}' is not in the models volume (see {_MODELS_GUIDE})"
        )
    entries = [entry for entry in sorted(directory.iterdir()) if entry.is_file() and not entry.is_symlink()]
    weights = [entry for entry in entries if entry.suffix.lower() == WEIGHT_SUFFIX]
    if not weights:
        if any(entry.suffix.lower() in PICKLE_SUFFIXES for entry in entries):
            raise ModelUnavailable(
                model,
                "unsafe_format",
                f"model directory '{label}' holds only pickle-based weights, which can run code and are refused; "
                "only safetensors is loaded",
            )
        raise ModelUnavailable(
            model, "no_weights", f"model directory '{label}' has no .safetensors weights (see {_MODELS_GUIDE})"
        )
    for weight in weights:
        try:
            check_content_format(weight, weight.name)
        except (FetchError, OSError) as exc:
            raise ModelUnavailable(
                model,
                "invalid_weights",
                f"{label}/{weight.name} is not a valid safetensors file ({exc.__class__.__name__})",
            ) from exc
    return weights


# Returns the verified model directory of a configured model. Raises ModelUnavailable when it is not configured,
# missing, empty, unsafe or invalid. The directory name doubles as the model label stored with its values.
def locate_model(config: dict[str, Any], model: str) -> Path:
    directory = models_root(config) / model_dir_name(config, model)
    verify_model_dir(directory, model)
    return directory
