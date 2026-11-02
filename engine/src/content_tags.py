"""Zero-shot content tags: the stored SigLIP 2 image vectors matched against the fixed label list.

One tag per photo, the label whose text vector is closest to the photo's vector. The list is fixed, holds no
sensitive class and no identity, and equals the list in docs/pipeline.md. A photo whose tag cannot be obtained
(no embedding, model without a text tower, unusable vectors) gets no tag; no value is invented.
"""

from collections.abc import Callable
from typing import Any

import numpy as np

from . import db
from . import progress_events as events
from .gpu_models import TextEmbedder, load_siglip2
from .model_loader import ModelUnavailable
from .neural_stages import Checkpoint, StageReport, _normalized, _report_unavailable
from .utils import Photo

CONTENT_TAGS = (
    "portrait", "group", "landscape", "cityscape", "architecture", "nature", "wildlife", "pet",
    "food", "event", "sport", "macro", "night", "abstract", "document", "other",
)  # fmt: skip
PROMPT_TEMPLATE = "a photo of {label}"


# Returns the L2-normalized text matrix of the labels, shape (len(CONTENT_TAGS), dim), or None when the encoder
# has no text method, fails, or returns a wrongly shaped or non-finite answer.
def _label_matrix(encoder: Any) -> np.ndarray | None:
    method = getattr(encoder, "embed_text", None)
    if method is None:
        events.log("The embedding model has no text tower; content tags stay unknown.", "warning")
        return None
    try:
        raw = np.asarray(method([PROMPT_TEMPLATE.format(label=label) for label in CONTENT_TAGS]))
    except Exception as exc:  # noqa: BLE001 - a failed text pass must not stop the run
        events.log(f"SigLIP 2 text encoding failed ({exc.__class__.__name__}); content tags stay unknown.", "warning")
        return None
    rows = [_normalized(row) for row in raw] if raw.ndim == 2 and raw.shape[0] == len(CONTENT_TAGS) else []
    if len(rows) != len(CONTENT_TAGS) or any(row is None for row in rows):
        events.log(f"SigLIP 2 returned unusable text vectors of shape {raw.shape}; content tags stay unknown.", "warning")
        return None
    return np.stack(rows)


# Tags the non-rejected photos that have a stored image embedding with the closest label of CONTENT_TAGS and
# stores it in `content_tags`, replacing earlier tags. `encoder` is loaded from the local model directory when not
# given. Returns a report; with no model or text tower every photo counts as unknown and nothing is stored.
# `checkpoint`, when given, is called once with the eligible photos after their tags are stored, and only when the
# label vectors were usable, so a missing model or text tower leaves the photos to be tagged by a later run.
def tag_content(
    photos: list[Photo],
    config: dict[str, Any],
    conn: Any,
    encoder: TextEmbedder | None = None,
    loader: Callable[[dict[str, Any]], TextEmbedder] = load_siglip2,
    checkpoint: Checkpoint | None = None,
) -> StageReport:
    eligible = [photo for photo in photos if photo.status != "rejected"]
    try:
        encoder = encoder or loader(config)
    except ModelUnavailable as exc:
        _report_unavailable(exc, "Content tags")
        return StageReport(0, len(eligible), exc)
    labels = _label_matrix(encoder)
    if labels is None:
        return StageReport(0, len(eligible))
    embeddings = db.load_embeddings(conn)
    stored = 0
    for photo in eligible:
        vector = embeddings.get(photo.id)
        image = _normalized(vector) if vector is not None else None
        if image is None or image.shape[0] != labels.shape[1]:
            continue
        similarities = labels @ image
        best = int(np.argmax(similarities))
        db.save_content_tag(conn, photo.id, CONTENT_TAGS[best], float(similarities[best]), encoder.name)
        stored += 1
    events.progress("content_tags", len(eligible), len(eligible), f"content tags {stored}/{len(eligible)}")
    if checkpoint is not None:
        checkpoint(eligible)
    return StageReport(stored, len(eligible) - stored)
