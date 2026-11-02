"""GPU batch stages: Q-ReAlign quality and aesthetic scores, and SigLIP 2 embeddings, over the working copies.

Both stages read only the upright working copy made at ingest, send photos to the model in batches of the
configured size, and store what comes back. A value that cannot be obtained (model missing, working copy
unreadable, a batch that fails, an answer outside its range) is stored as unknown (NULL, or no embedding row).
No stage ever substitutes a neutral number, a colour histogram or any other made-up value.

The models are passed in or loaded by gpu_models, which never downloads anything. A missing model ends the
stage with a structured error event and a StageReport; the rest of the pipeline continues.
"""

import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from . import db
from . import progress_events as events
from .gpu_models import AESTHETIC, QUALITY, BatchEmbedder, BatchScorer, load_qrealign, load_siglip2
from .model_loader import ModelUnavailable, model_dir_name
from .utils import Photo, working_copy_path

DEFAULT_BATCH_SIZE = 16
MAX_BATCH_SIZE = 256
# Models return probabilities and unit vectors, so only rounding noise this small is forgiven.
_RANGE_TOLERANCE = 1e-6
_MIN_NORM = 1e-8


@dataclass(frozen=True)
class StageReport:
    """What a stage did: values stored, photos left unknown, and the error that stopped the model, if any."""

    stored: int
    unknown: int
    error: ModelUnavailable | None = None


# Returns the batch size of a model from config (models.<model>.batch_size), clamped to 1..MAX_BATCH_SIZE.
def batch_size(config: dict[str, Any], model: str) -> int:
    raw = (config.get("models", {}).get(model) or {}).get("batch_size", DEFAULT_BATCH_SIZE)
    try:
        return max(1, min(MAX_BATCH_SIZE, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_BATCH_SIZE


# Returns the stage's model label from config without touching the disk; used when the model is missing.
def _label(config: dict[str, Any], model: str) -> str:
    try:
        return model_dir_name(config, model)
    except ModelUnavailable:
        return ""


# Splits a list into consecutive chunks of at most `size` items, in order.
def _chunks(items: Sequence[Photo], size: int) -> Iterator[Sequence[Photo]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# Opens the working copies of a chunk as RGB images. Returns the photos that could be read, with their images;
# a missing or undecodable copy is left out so the caller marks it unknown.
def _open_images(chunk: Sequence[Photo], config: dict[str, Any]) -> tuple[list[Photo], list[Image.Image]]:
    readable: list[Photo] = []
    images: list[Image.Image] = []
    for photo in chunk:
        try:
            with Image.open(working_copy_path(config, photo.id)) as image:
                images.append(image.convert("RGB"))
            readable.append(photo)
        except (OSError, ValueError):
            events.log(f"No readable working copy for {photo.id[:12]}; its values stay unknown.", "warning")
    return readable, images


# Returns the value as a float in [0, 1], or None when it is not a finite number in range (up to rounding noise).
def _unit_value(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < -_RANGE_TOLERANCE or number > 1.0 + _RANGE_TOLERANCE:
        return None
    return min(1.0, max(0.0, number))


# Runs one scoring task on a batch. Returns one value per image, None where the answer is unusable; a raised
# error or an answer of the wrong length makes the whole batch unknown.
def _score_task(scorer: BatchScorer, images: list[Image.Image], task: str) -> list[float | None]:
    try:
        raw = list(scorer.score(images, task))
    except Exception as exc:  # noqa: BLE001 - a failed batch (for example out of GPU memory) must not stop the run
        events.log(
            f"Q-ReAlign {task} batch of {len(images)} failed ({exc.__class__.__name__}); values stay unknown.",
            "warning",
        )
        return [None] * len(images)
    if len(raw) != len(images):
        events.log(f"Q-ReAlign {task} returned {len(raw)} values for {len(images)} images; ignored.", "warning")
        return [None] * len(images)
    return [_unit_value(value) for value in raw]


# Scores the non-rejected photos with Q-ReAlign, in batches, for quality and aesthetics, and stores values between
# 0 and 1 in `neural_scores`. `scorer` is loaded from the local model directory when not given. When the model
# cannot be loaded, every photo is stored as unknown, a structured error event is emitted and the report carries
# the error; no value is invented. Rejected photos are skipped.
def score_qrealign(
    photos: list[Photo],
    config: dict[str, Any],
    conn: Any,
    scorer: BatchScorer | None = None,
    loader: Callable[[dict[str, Any]], BatchScorer] = load_qrealign,
) -> StageReport:
    eligible = [photo for photo in photos if photo.status != "rejected"]
    try:
        scorer = scorer or loader(config)
    except ModelUnavailable as exc:
        _report_unavailable(exc, "Q-ReAlign scores")
        label = _label(config, "qrealign")
        for photo in eligible:
            db.save_neural_scores(conn, photo.id, label, None, None)
        return StageReport(0, len(eligible), exc)
    events.log(f"Q-ReAlign scoring {len(eligible)} photos on {scorer.device}.", "info")
    stored = unknown = done = 0
    for chunk in _chunks(eligible, batch_size(config, "qrealign")):
        readable, images = _open_images(chunk, config)
        qualities = _score_task(scorer, images, QUALITY) if images else []
        aesthetics = _score_task(scorer, images, AESTHETIC) if images else []
        values = {photo.id: (q, a) for photo, q, a in zip(readable, qualities, aesthetics)}
        for photo in chunk:
            quality, aesthetic = values.get(photo.id, (None, None))
            db.save_neural_scores(conn, photo.id, scorer.name, quality, aesthetic)
            if quality is None and aesthetic is None:
                unknown += 1
            else:
                stored += 1
        done += len(chunk)
        events.progress("qrealign", done, len(eligible), f"quality and aesthetics {done}/{len(eligible)}")
    return StageReport(stored, unknown)


# Returns the vector divided by its length as float32, or None when it is not finite or has no length.
def _normalized(vector: np.ndarray) -> np.ndarray | None:
    vec = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(vec))
    if vec.ndim != 1 or vec.size == 0 or not np.all(np.isfinite(vec)) or norm < _MIN_NORM:
        return None
    return (vec / norm).astype(np.float32)


# Embeds the non-rejected photos with SigLIP 2, in batches, and stores one L2-normalized float32 vector per photo
# in `embeddings`. `embedder` is loaded from the local model directory when not given. A photo whose vector cannot
# be obtained gets no embedding row, so grouping skips it. When the model cannot be loaded, a structured error
# event is emitted and the report carries the error; no substitute descriptor is computed. Rejected photos are
# skipped.
def embed_siglip2(
    photos: list[Photo],
    config: dict[str, Any],
    conn: Any,
    embedder: BatchEmbedder | None = None,
    loader: Callable[[dict[str, Any]], BatchEmbedder] = load_siglip2,
) -> StageReport:
    eligible = [photo for photo in photos if photo.status != "rejected"]
    try:
        embedder = embedder or loader(config)
    except ModelUnavailable as exc:
        _report_unavailable(exc, "SigLIP 2 embeddings")
        return StageReport(0, len(eligible), exc)
    events.log(f"SigLIP 2 embedding {len(eligible)} photos on {embedder.device}.", "info")
    stored = unknown = done = 0
    for chunk in _chunks(eligible, batch_size(config, "siglip2")):
        readable, images = _open_images(chunk, config)
        vectors = _embed_batch(embedder, images) if images else []
        for photo, vector in zip(readable, vectors):
            if vector is None:
                continue
            db.save_embedding(conn, photo.id, vector, embedder.name)
            stored += 1
        done += len(chunk)
        events.progress("siglip2", done, len(eligible), f"embeddings {done}/{len(eligible)}")
    return StageReport(stored, len(eligible) - stored)


# Embeds one batch. Returns one normalized vector per image, None where unusable; a raised error or an array of
# the wrong shape makes the whole batch unknown.
def _embed_batch(embedder: BatchEmbedder, images: list[Image.Image]) -> list[np.ndarray | None]:
    try:
        raw = np.asarray(embedder.embed(images))
    except Exception as exc:  # noqa: BLE001 - a failed batch (for example out of GPU memory) must not stop the run
        events.log(
            f"SigLIP 2 batch of {len(images)} failed ({exc.__class__.__name__}); embeddings stay unknown.",
            "warning",
        )
        return [None] * len(images)
    if raw.ndim != 2 or raw.shape[0] != len(images):
        events.log(f"SigLIP 2 returned an array of shape {raw.shape} for {len(images)} images; ignored.", "warning")
        return [None] * len(images)
    return [_normalized(row) for row in raw]


# Emits the structured error of an unavailable model: its code, the model key and the remedy in the message.
def _report_unavailable(error: ModelUnavailable, what: str) -> None:
    events.emit(
        {
            "type": "log",
            "level": "error",
            "code": f"model_{error.code}",
            "model": error.model,
            "message": f"{what} are unknown: {error.message}",
        }
    )
