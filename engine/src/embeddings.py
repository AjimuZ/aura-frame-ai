"""Local whole-frame image embeddings for clustering, near-similar grouping, and
diversity-aware reranking.

Uses OpenCLIP (CPU) when torch/open_clip are installed; otherwise falls back to a
deterministic lightweight color/structure descriptor so the pipeline still runs without
the heavy ML stack. These embeddings describe the entire frame for diversity only - they
are NOT face or identity embeddings (see project plan P1.10 / P2).
"""

from typing import Any

import numpy as np
from PIL import Image

from . import db
from . import progress_events as events
from .utils import Photo, working_copy_path

_FALLBACK_MODEL = "fallback-colorhist"

# Lazy singletons so the (slow) model load happens at most once per process.
_clip_state: dict[str, Any] | None = None


# Loads the OpenCLIP model once; returns None to signal the lightweight fallback path.
def _load_clip(config: dict[str, Any]) -> dict[str, Any] | None:
    global _clip_state
    if _clip_state is not None:
        return _clip_state if _clip_state else None
    try:
        import open_clip  # type: ignore
        import torch  # type: ignore

        cfg = config.get("embeddings", {})
        model_name = cfg.get("model", "ViT-B-32")
        pretrained = cfg.get("pretrained", "laion2b_s34b_b79k")
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained
        )
        model.eval()
        _clip_state = {"model": model, "preprocess": preprocess, "torch": torch, "name": model_name}
        return _clip_state
    except Exception as exc:  # noqa: BLE001 - missing torch/weights -> fallback
        events.log(f"OpenCLIP unavailable ({exc}); using lightweight embeddings.", "info")
        _clip_state = {}
        return None


# Deterministic fallback descriptor: normalized RGB histogram of a downscaled image.
def _fallback_embedding(image: Image.Image) -> np.ndarray:
    small = image.convert("RGB").resize((64, 64))
    arr = np.asarray(small, dtype=np.float32)
    hist = np.concatenate(
        [np.histogram(arr[:, :, c], bins=16, range=(0, 255))[0] for c in range(3)]
    ).astype(np.float32)
    norm = np.linalg.norm(hist) + 1e-8
    return hist / norm


# Computes a single L2-normalized embedding for a photo using CLIP or the fallback. It reads the
# upright working copy made at ingest (so orientation is already applied), never the original.
# Raises OSError when the working copy is missing.
def compute_embedding(
    photo: Photo, state: dict[str, Any] | None, config: dict[str, Any]
) -> tuple[np.ndarray, str]:
    with Image.open(working_copy_path(config, photo.id)) as image:
        image.load()
        if state is None:
            return _fallback_embedding(image), _FALLBACK_MODEL
        torch = state["torch"]
        tensor = state["preprocess"](image.convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            feats = state["model"].encode_image(tensor)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.squeeze(0).cpu().numpy().astype(np.float32), state["name"]


# Embeds a list of photos and persists the vectors to the database.
def embed_photos(photos: list[Photo], config: dict[str, Any], conn: Any) -> None:
    state = _load_clip(config)
    total = len(photos)
    for i, photo in enumerate(photos, start=1):
        if photo.status == "rejected":
            continue
        try:
            vector, model = compute_embedding(photo, state, config)
            db.save_embedding(conn, photo.id, vector, model)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
            events.log(f"Embedding failed for {photo.id}: {exc}", "warning")
        if i % 10 == 0 or i == total:
            events.progress("embeddings", i, total, f"embeddings {i}/{total}")


# Greedy agglomerative clustering by cosine distance; assigns cluster_id to each photo.
def assign_clusters(photos: list[Photo], config: dict[str, Any], conn: Any) -> int:
    vectors = db.load_embeddings(conn)
    distance = float(config.get("embeddings", {}).get("cluster_distance", 0.12))
    centroids: list[np.ndarray] = []
    for photo in photos:
        vec = vectors.get(photo.id)
        if vec is None or photo.status == "rejected":
            continue
        assigned = -1
        for idx, centroid in enumerate(centroids):
            if _cosine_distance(vec, centroid) <= distance:
                assigned = idx
                break
        if assigned == -1:
            centroids.append(vec)
            assigned = len(centroids) - 1
        photo.cluster_id = assigned
    return len(centroids)


# Cosine distance between two vectors (0 = identical direction, 2 = opposite).
def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
    return float(1.0 - float(np.dot(a, b)) / denom)
