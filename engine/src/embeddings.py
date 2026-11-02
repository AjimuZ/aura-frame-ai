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


_DEFAULT_CLUSTER_DISTANCE = 0.12
_DEFAULT_CLUSTER_SECONDS = 300.0
_TIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


# Capture time in seconds (naive wall clock) from EXIF DateTimeOriginal, else created_at; None when
# missing or unparsable, so such a photo is grouped by embedding alone.
def _capture_seconds(photo: Photo) -> float | None:
    from datetime import datetime

    for raw in (photo.exif.get("DateTimeOriginal"), photo.created_at):
        text = str(raw).strip()[:19] if raw else ""
        for fmt in _TIME_FORMATS:
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
    return None


# Groups non-rejected photos that have an embedding; sets cluster_id on each and returns the group
# count. Method: complete linkage. Two photos are compatible when their cosine distance is at most
# embeddings.cluster_distance and, when both have capture times, those are at most
# embeddings.cluster_seconds apart. Compatible pairs are merged closest first (ties by photo id),
# and two groups merge only if EVERY cross pair is compatible, so a chain of look-alikes cannot
# drag unrelated photos together. Input is sorted by id first, so the partition does not depend on
# input order; groups are numbered by their smallest member id. Vectors of different sizes are never
# compared. Photos without a usable embedding keep cluster_id -1.
def assign_clusters(photos: list[Photo], config: dict[str, Any], conn: Any) -> int:
    cfg = config.get("embeddings", {})
    distance = float(cfg.get("cluster_distance", _DEFAULT_CLUSTER_DISTANCE))
    window = float(cfg.get("cluster_seconds", _DEFAULT_CLUSTER_SECONDS))
    vectors = db.load_embeddings(conn)
    members = sorted(
        (p for p in photos if p.status != "rejected" and vectors.get(p.id) is not None),
        key=lambda p: p.id,
    )
    for photo in photos:
        photo.cluster_id = -1
    n = len(members)
    times = [_capture_seconds(p) for p in members]

    edges: list[tuple[float, int, int]] = []
    by_shape: dict[tuple[int, ...], list[int]] = {}
    for idx, photo in enumerate(members):
        by_shape.setdefault(vectors[photo.id].shape, []).append(idx)
    for indices in by_shape.values():
        matrix = np.stack([vectors[members[i].id] for i in indices]).astype(np.float64)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8
        for row, i in enumerate(indices):
            dists = 1.0 - matrix[row + 1 :] @ matrix[row]
            for offset in np.nonzero(dists <= distance)[0]:
                j = indices[row + 1 + int(offset)]
                if times[i] is not None and times[j] is not None and abs(times[i] - times[j]) > window:
                    continue
                edges.append((round(float(dists[offset]), 9), i, j))
    edges.sort()

    compatible = {(i, j) for _, i, j in edges}
    groups: dict[int, list[int]] = {i: [i] for i in range(n)}
    owner = list(range(n))
    for _, i, j in edges:
        a, b = owner[i], owner[j]
        if a == b:
            continue
        if all((min(x, y), max(x, y)) in compatible for x in groups[a] for y in groups[b]):
            for x in groups[b]:
                owner[x] = a
            groups[a].extend(groups.pop(b))

    # members is sorted by id, so the smallest index in a group is its smallest member id.
    ordered = sorted(groups.values(), key=min)
    for cluster_id, group in enumerate(ordered):
        for idx in group:
            members[idx].cluster_id = cluster_id
    return len(ordered)


# Cosine distance between two vectors (0 = identical direction, 2 = opposite).
def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
    return float(1.0 - float(np.dot(a, b)) / denom)
