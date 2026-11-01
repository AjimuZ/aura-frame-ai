"""Lightweight local aesthetic pre-scoring (LAION aesthetic predictor style).

The LAION aesthetic predictor is a small linear MLP on top of CLIP image embeddings.
If local weights are present (config aesthetic.weights_path) and torch is installed, we
use them; otherwise we fall back to a neutral score derived from simple image statistics.
No weights are ever downloaded automatically.
"""

from typing import Any

import numpy as np

from . import db
from . import progress_events as events

_predictor: Any | None = None
_loaded = False


# Loads the optional aesthetic MLP once; returns None to use the neutral fallback.
def _load_predictor(config: dict[str, Any]) -> Any | None:
    global _predictor, _loaded
    if _loaded:
        return _predictor
    _loaded = True
    cfg = config.get("aesthetic", {})
    weights_path = cfg.get("weights_path", "")
    try:
        import os

        import torch  # type: ignore
        from torch import nn  # type: ignore

        if not weights_path or not os.path.exists(weights_path):
            events.log("Aesthetic weights not found; using neutral aesthetic score.", "info")
            _predictor = None
            return None

        # LAION predictor architecture (input dim 768 for ViT-L; 512 for ViT-B/32).
        class _MLP(nn.Module):
            def __init__(self, dim: int) -> None:
                super().__init__()
                self.layers = nn.Sequential(
                    nn.Linear(dim, 1024), nn.Dropout(0.2), nn.Linear(1024, 128),
                    nn.Dropout(0.2), nn.Linear(128, 64), nn.Dropout(0.1), nn.Linear(64, 16),
                    nn.Linear(16, 1),
                )

            def forward(self, x: Any) -> Any:
                return self.layers(x)

        # weights_only=True blocks arbitrary pickle execution from the downloaded .pth; the
        # LAION predictor file is a plain tensor state_dict, so this loads it without issue.
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
        dim = next(iter(state.values())).shape[1] if state else 512
        model = _MLP(dim)
        model.load_state_dict(state)
        model.eval()
        _predictor = {"model": model, "torch": torch}
        return _predictor
    except Exception as exc:  # noqa: BLE001 - torch/weights optional
        events.log(f"Aesthetic predictor unavailable ({exc}); neutral score used.", "info")
        _predictor = None
        return None


# Neutral fallback aesthetic score (0-100) from embedding spread when no model is present.
def _fallback_score(vector: np.ndarray) -> float:
    spread = float(np.std(vector)) * 1000.0
    return float(np.clip(45.0 + spread, 30.0, 70.0))


# Scores aesthetics for all non-rejected photos using stored embeddings.
def score_aesthetic(photos: list[Any], config: dict[str, Any], conn: Any) -> None:
    predictor = _load_predictor(config)
    vectors = db.load_embeddings(conn)
    total = len(photos)
    for i, photo in enumerate(photos, start=1):
        if photo.status == "rejected":
            continue
        vec = vectors.get(photo.id)
        if vec is None:
            photo.aesthetic_score = 50.0
        elif predictor is None:
            photo.aesthetic_score = _fallback_score(vec)
        else:
            torch = predictor["torch"]
            with torch.no_grad():
                raw = float(predictor["model"](torch.from_numpy(vec).float().unsqueeze(0)).item())
            # LAION scores are ~1-10; map to 0-100.
            photo.aesthetic_score = float(np.clip(raw * 10.0, 0.0, 100.0))
        if i % 10 == 0 or i == total:
            events.progress("aesthetic", i, total, f"aesthetic {i}/{total}")
