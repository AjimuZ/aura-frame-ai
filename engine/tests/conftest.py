"""Pytest fixtures for the AuraFrame AI engine.

All images used in tests are SYNTHETIC (generated here). No personal photos are ever
read or used.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# Make the engine `src` package importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import load_config  # noqa: E402


# Saves a numpy RGB array as a JPEG and returns the path.
def _save(array: np.ndarray, path: Path) -> Path:
    Image.fromarray(array.astype("uint8")).save(path, "JPEG", quality=90)
    return path


@pytest.fixture()
def config() -> dict:
    return load_config()


# A config whose generated files (working copies, thumbnails, database) all live under tmp_path.
@pytest.fixture()
def data_config(config: dict, tmp_path: Path) -> dict:
    from src.utils import apply_data_root

    return apply_data_root(config, str(tmp_path / "dataroot"))


@pytest.fixture()
def sharp_image(tmp_path: Path) -> Path:
    # High-frequency checkerboard => high Laplacian variance (sharp).
    base = np.indices((1000, 1000)).sum(axis=0) % 2 * 255
    rgb = np.stack([base, base, base], axis=-1)
    return _save(rgb, tmp_path / "sharp.jpg")


@pytest.fixture()
def blurry_image(tmp_path: Path) -> Path:
    # Uniform mid-gray => near-zero variance (blurry/flat).
    rgb = np.full((1000, 1000, 3), 128, dtype="uint8")
    return _save(rgb, tmp_path / "blurry.jpg")


@pytest.fixture()
def heic_image(tmp_path: Path) -> Path:
    # A synthetic HEIC file to verify HEIC ingestion works end-to-end.
    cols = np.indices((1000, 1000))[1]
    base = ((cols // 6) % 2) * 180 + 30
    rgb = np.stack([base, base * 0.6, base * 0.4], axis=-1).astype("uint8")
    path = tmp_path / "photo.heic"
    Image.fromarray(rgb).save(path, format="HEIF")
    return path


@pytest.fixture()
def dark_image(tmp_path: Path) -> Path:
    # Dark but textured (wide stripes survive JPEG), so it is rejected for darkness
    # rather than for being flat/blurry.
    cols = np.indices((1000, 1000))[1]
    base = ((cols // 8) % 2) * 28
    rgb = np.stack([base, base, base], axis=-1)
    return _save(rgb, tmp_path / "dark.jpg")
