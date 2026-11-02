"""Tests of the sanity gate: only objective failures reject, everything else is kept.

Every image is synthetic and lives under pytest's tmp_path; no personal photo is read.
"""

from pathlib import Path

import numpy as np
from PIL import Image

from src.index_photos import index_photos
from src.local_gate import run_local_gate
from src.quality_score import score_quality
from src.utils import Photo, working_copy_path


# Saves an RGB array as an image whose format follows the file suffix and returns the path.
def _save(array: np.ndarray, path: Path) -> Path:
    Image.fromarray(array.astype("uint8")).save(path)
    return path


# Runs the same steps as an analysis (index, measure, gate) and returns the photos by file name.
def _gate_folder(folder: Path, config: dict) -> dict[str, Photo]:
    photos = index_photos(folder, config, include_subfolders=False)
    for photo in photos:
        if photo.status != "rejected":
            score_quality(photo, config)
    run_local_gate(photos, config)
    return {p.file_name: p for p in photos}


# A smooth gradient has almost no Laplacian response and a black image with a few bright points
# has a near-zero mean, yet neither is a defect: both survive the gate.
def test_a_smooth_gradient_and_a_black_image_with_bright_points_survive(
    tmp_path: Path, data_config: dict
) -> None:
    ramp = np.tile(np.linspace(20, 235, 1000), (800, 1))
    _save(np.stack([ramp, ramp, ramp], axis=-1), tmp_path / "gradient.jpg")
    sky = np.zeros((800, 1000, 3))
    rng = np.random.default_rng(3)
    sky[rng.integers(0, 800, 40), rng.integers(0, 1000, 40)] = 255
    _save(sky, tmp_path / "sky.jpg")

    result = _gate_folder(tmp_path, data_config)

    gradient, stars = result["gradient.jpg"], result["sky.jpg"]
    assert gradient.laplacian_variance < 35  # below the old blur-reject threshold
    assert stars.brightness_mean < 10  # below the old brightness-reject threshold
    for photo in (gradient, stars):
        assert photo.status != "rejected"
        assert photo.gate_status not in {"hard_reject", "failed_read"}
        assert photo.eligible_for_openai


# A corrupt file is recorded as unreadable and the rest of the folder is still ingested.
def test_a_corrupt_file_is_recorded_and_the_run_continues(
    tmp_path: Path, data_config: dict
) -> None:
    (tmp_path / "a_broken.jpg").write_bytes(b"\xff\xd8\xff\xe0 truncated, not a real jpeg")
    _save(np.random.default_rng(1).integers(0, 256, (400, 600, 3)), tmp_path / "b_good.jpg")

    result = _gate_folder(tmp_path, data_config)

    assert set(result) == {"a_broken.jpg", "b_good.jpg"}
    broken = result["a_broken.jpg"]
    assert broken.reject_reason == "unreadable_image"
    assert broken.gate_status == "failed_read" and not broken.eligible_for_openai
    assert result["b_good.jpg"].gate_status not in {"hard_reject", "failed_read"}


# A byte-identical copy, an unsupported format and a tiny image are each rejected, with a reason
# that tells them apart, while the original photo next to them is kept.
def test_duplicate_unsupported_and_tiny_images_are_rejected(
    tmp_path: Path, data_config: dict
) -> None:
    original = _save(
        np.random.default_rng(2).integers(0, 256, (500, 700, 3)), tmp_path / "a_original.jpg"
    )
    (tmp_path / "b_copy.jpg").write_bytes(original.read_bytes())
    # A GIF stored under a supported extension: Pillow reads it, the pipeline does not accept it.
    Image.fromarray(np.full((400, 600, 3), 90, dtype="uint8")).save(tmp_path / "c_anim.jpg", "GIF")
    _save(np.random.default_rng(4).integers(0, 256, (200, 255, 3)), tmp_path / "d_tiny.png")

    result = _gate_folder(tmp_path, data_config)

    assert result["a_original.jpg"].gate_status not in {"hard_reject", "failed_read"}
    copy = result["b_copy.jpg"]
    assert (copy.gate_status, copy.hard_reject_reason) == ("hard_reject", "exact_duplicate")
    assert copy.id != result["a_original.jpg"].id
    gif = result["c_anim.jpg"]
    assert (gif.gate_status, gif.hard_reject_reason) == ("hard_reject", "unsupported_format")
    assert not working_copy_path(data_config, gif.id).exists()
    tiny = result["d_tiny.png"]
    assert (tiny.gate_status, tiny.hard_reject_reason) == ("hard_reject", "too_small")
    assert not any(p.eligible_for_openai for p in (copy, gif, tiny))


# 256 px on the long side is kept, 255 px is rejected, whichever side is the long one.
def test_the_size_limit_is_on_the_long_side(tmp_path: Path, data_config: dict) -> None:
    rng = np.random.default_rng(5)
    _save(rng.integers(0, 256, (60, 256, 3)), tmp_path / "wide_256.png")
    _save(rng.integers(0, 256, (255, 40, 3)), tmp_path / "tall_255.png")

    result = _gate_folder(tmp_path, data_config)

    assert result["wide_256.png"].gate_status not in {"hard_reject", "failed_read"}
    assert result["tall_255.png"].hard_reject_reason == "too_small"


# Dark, bright and blurry photos are never rejected; a screenshot only gets a flag.
def test_brightness_blur_and_screenshots_never_reject(tmp_path: Path, data_config: dict) -> None:
    _save(np.full((800, 1000, 3), 8), tmp_path / "dark.jpg")
    _save(np.full((800, 1000, 3), 250), tmp_path / "bright.jpg")
    _save(np.full((800, 1000, 3), 128), tmp_path / "flat.jpg")
    # Flat colour blocks on 8 px boundaries stay flat after the JPEG working copy, like a UI capture.
    interface = np.full((800, 1000, 3), 240)
    interface[:128] = (40, 90, 200)
    interface[304:496, 208:800] = (255, 255, 255)
    _save(interface, tmp_path / "capture.png")

    result = _gate_folder(tmp_path, data_config)

    for photo in result.values():
        assert photo.status != "rejected"
        assert photo.gate_status not in {"hard_reject", "failed_read"}
        assert photo.eligible_for_openai
    assert "local_flag_low_light" in result["dark.jpg"].soft_flags
    assert "local_flag_low_light" in result["bright.jpg"].soft_flags
    capture = result["capture.png"]
    assert capture.likely_screenshot
    assert "local_flag_screenshot" in capture.soft_flags
    assert "local_flag_screenshot" not in result["flat.jpg"].soft_flags
