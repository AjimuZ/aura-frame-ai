"""Technical quality scoring: blur, brightness, contrast, and resolution.

Scores are normalized to 0-100. The combined technical_score drives the pre-ranking
stage so only strong candidates reach the AI model.
"""

from typing import Any

import cv2
import numpy as np

from .utils import Photo, read_working_bgr


# Computes the variance of the Laplacian as a sharpness measure (higher = sharper).
def blur_variance(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# Scores sharpness on 0-100 relative to the configured blur threshold.
def _blur_score(variance: float, threshold: float) -> float:
    return float(np.clip((variance / max(threshold, 1.0)) * 60.0, 0.0, 100.0))


# Scores brightness on 0-100, peaking at mid-tones and penalizing dark/blown images.
def _brightness_score(mean: float, min_b: float, max_b: float) -> float:
    if mean < min_b or mean > max_b:
        return float(np.clip(40.0 - abs(mean - 128.0) * 0.3, 0.0, 100.0))
    return float(np.clip(100.0 - abs(mean - 128.0) * 0.6, 0.0, 100.0))


# Scores contrast on 0-100 from pixel standard deviation.
def _contrast_score(std: float) -> float:
    return float(np.clip(std / 70.0 * 100.0, 0.0, 100.0))


# Scores resolution on 0-100 relative to the configured minimum dimensions.
def _resolution_score(width: int, height: int, min_w: int, min_h: int) -> float:
    if width <= 0 or height <= 0:
        return 0.0
    ratio = min(width / max(min_w, 1), height / max(min_h, 1))
    return float(np.clip(ratio * 70.0, 0.0, 100.0))


# Lightweight heuristic for screenshots / non-photos: missing camera EXIF combined with a
# clean, low-detail image (few unique colors) is typical of UI captures rather than photos.
def _looks_like_screenshot(photo: Any, gray: np.ndarray) -> bool:
    has_camera = bool(photo.exif.get("Make") or photo.exif.get("Model"))
    if has_camera:
        return False
    suffix = photo.file_name.lower().rsplit(".", 1)[-1] if "." in photo.file_name else ""
    unique_ratio = float(np.unique(gray).size) / 256.0
    edge_density = float(np.mean(np.abs(np.diff(gray.astype(np.int16), axis=1)) > 30))
    return suffix == "png" and unique_ratio < 0.35 and edge_density < 0.06


# Computes all quality metrics for a single photo and updates it in place. It reads the upright
# working copy made at ingest (long side at most 1,536 px), never the original, so sharpness is
# measured at that scale; a photo without a working copy is marked as unreadable.
def score_quality(photo: Photo, config: dict[str, Any]) -> Photo:
    filters = config["filters"]
    image = read_working_bgr(photo, config)
    if image is None:
        photo.status = "rejected"
        photo.reject_reason = "unreadable_image"
        return photo

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    variance = blur_variance(gray)
    mean = float(gray.mean())
    std = float(gray.std())

    photo.blur_score = _blur_score(variance, float(filters["blur_threshold"]))
    photo.brightness_score = _brightness_score(
        mean, float(filters["min_brightness"]), float(filters["max_brightness"])
    )
    photo.contrast_score = _contrast_score(std)
    photo.resolution_score = _resolution_score(
        photo.width, photo.height, int(filters["min_width"]), int(filters["min_height"])
    )

    photo.technical_score = (
        photo.blur_score * 0.4
        + photo.brightness_score * 0.25
        + photo.contrast_score * 0.15
        + photo.resolution_score * 0.2
    )

    photo.likely_screenshot = _looks_like_screenshot(photo, gray)

    # Store the raw measurements the Local Minimal Gate needs. score_quality only MEASURES;
    # it never discards a photo (the gate owns all reject/flag policy now), except when the
    # file genuinely cannot be read, which is handled above as a failed read.
    photo.brightness_mean = mean
    photo.laplacian_variance = variance

    return photo
