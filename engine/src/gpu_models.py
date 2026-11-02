"""GPU adapters for Q-ReAlign Mini and SigLIP 2, loaded from local safetensors only.

Each adapter turns a batch of PIL images into numbers and nothing else; batching, validation and storage live
in neural_stages.py, which accepts any object with the same two methods (tests pass fakes). torch and
transformers are imported inside the loaders, so importing this module needs neither.

No code path here downloads anything: a model is read from a directory that model_loader has verified, with
`local_files_only=True`, `use_safetensors=True` and `trust_remote_code=False`. Behaviour with the real
checkpoints is unverified until P04.MEASURE; sessions never fetch weights.
"""

from typing import Any, Protocol, Sequence

import numpy as np
from PIL import Image

from .model_loader import ModelUnavailable, locate_model

QUALITY = "quality"
AESTHETIC = "aesthetic"
# Rating words in the order of LEVEL_VALUES, each with the leading space of a word that continues a sentence.
LEVEL_WORDS = (" excellent", " good", " fair", " poor", " bad")
LEVEL_VALUES = (1.0, 0.75, 0.5, 0.25, 0.0)
_PROMPTS = {
    QUALITY: ("How would you rate the quality of this image?", "The quality of the image is"),
    AESTHETIC: ("How would you rate the aesthetics of this image?", "The aesthetics of the image are"),
}
_DEVICE_CHOICES = ("auto", "cuda", "cpu")


class BatchScorer(Protocol):
    """Scores a batch of images for one task."""

    name: str
    device: str

    # Returns one value per image, in order, between 0 and 1. `task` is QUALITY or AESTHETIC.
    def score(self, images: Sequence[Image.Image], task: str) -> list[float]: ...


class BatchEmbedder(Protocol):
    """Embeds a batch of images."""

    name: str
    device: str

    # Returns an array of shape (len(images), dim), one row per image, in order.
    def embed(self, images: Sequence[Image.Image]) -> np.ndarray: ...


# Returns the probability-weighted value of the five rating levels, between 0 and 1: the sum of each level's
# probability times its weight (excellent 1, good 0.75, fair 0.5, poor 0.25, bad 0). The probabilities are
# renormalized to sum to 1. Raises ValueError for a wrong length, a negative or non-finite entry, or a zero sum.
def weighted_level_value(probabilities: Sequence[float]) -> float:
    probs = np.asarray(probabilities, dtype=np.float64)
    if probs.shape != (len(LEVEL_VALUES),) or not np.all(np.isfinite(probs)) or np.any(probs < 0):
        raise ValueError("expected five finite, non-negative level probabilities")
    total = float(probs.sum())
    if total <= 0:
        raise ValueError("the level probabilities sum to zero")
    return float(np.dot(probs / total, LEVEL_VALUES))


# Returns the device to run on: "cuda" or "cpu". "auto" takes cuda when `torch` reports it; an explicit "cuda"
# on a host without it raises ModelUnavailable (cuda_unavailable) instead of silently running on the CPU.
def resolve_device(requested: str, torch: Any, model: str = "") -> str:
    if requested not in _DEVICE_CHOICES:
        raise ModelUnavailable(model, "not_configured", f"models.device must be one of {', '.join(_DEVICE_CHOICES)}")
    has_cuda = bool(torch.cuda.is_available())
    if requested == "cuda" and not has_cuda:
        raise ModelUnavailable(model, "cuda_unavailable", "models.device is cuda, but no CUDA device is available")
    return "cuda" if requested == "cuda" or (requested == "auto" and has_cuda) else "cpu"


# Imports torch and transformers, or raises ModelUnavailable (dependency_missing) with the remedy.
def _import_stack(model: str) -> tuple[Any, Any]:
    try:
        import torch  # type: ignore
        import transformers  # type: ignore
    except ImportError as exc:
        raise ModelUnavailable(
            model,
            "dependency_missing",
            "torch and transformers are not installed; the api image `full` stage installs engine/requirements-gpu.txt",
        ) from exc
    return torch, transformers


# Moves a processor output to the device; floating-point tensors take the model's dtype.
def _to_device(inputs: Any, device: str, dtype: Any) -> dict[str, Any]:
    return {
        key: (value.to(device=device, dtype=dtype) if value.is_floating_point() else value.to(device))
        for key, value in dict(inputs).items()
    }


class QRealignScorer:
    """Q-ReAlign Mini: the rating-word probabilities after a short prompt give a value between 0 and 1."""

    def __init__(self, torch: Any, model: Any, processor: Any, level_ids: list[int], device: str, name: str) -> None:
        self._torch = torch
        self._model = model
        self._processor = processor
        self._level_ids = level_ids
        self.device = device
        self.name = name

    # Returns one 0 to 1 value per image for the task, in a single forward pass over the whole batch.
    def score(self, images: Sequence[Image.Image], task: str) -> list[float]:
        question, lead = _PROMPTS[task]
        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": question}]}]
        prompt = self._processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False) + lead
        inputs = self._processor(text=[prompt] * len(images), images=list(images), return_tensors="pt", padding=True)
        inputs = _to_device(inputs, self.device, self._model.dtype)
        with self._torch.inference_mode():
            logits = self._model(**inputs).logits[:, -1, :]
        probs = self._torch.softmax(logits[:, self._level_ids].float(), dim=-1).cpu().numpy()
        return [weighted_level_value(row) for row in probs]


class SigLIP2Embedder:
    """SigLIP 2 image tower: one vector per whole frame. It never looks for faces or identities."""

    def __init__(self, torch: Any, model: Any, processor: Any, device: str, name: str) -> None:
        self._torch = torch
        self._model = model
        self._processor = processor
        self.device = device
        self.name = name

    # Returns an array of shape (len(images), dim); the caller normalizes and validates it.
    def embed(self, images: Sequence[Image.Image]) -> np.ndarray:
        inputs = self._processor(images=list(images), return_tensors="pt")
        inputs = _to_device(inputs, self.device, self._model.dtype)
        with self._torch.inference_mode():
            features = self._model.get_image_features(**inputs)
        if not self._torch.is_tensor(features):  # some releases return an output object
            features = features.pooler_output
        return features.float().cpu().numpy()


# Loads Q-ReAlign Mini from its verified local directory onto the configured device, in half precision on cuda.
# Raises ModelUnavailable for any missing, unsafe or unusable part; nothing is downloaded.
def load_qrealign(config: dict[str, Any]) -> QRealignScorer:
    directory = locate_model(config, "qrealign")
    torch, transformers = _import_stack("qrealign")
    device = resolve_device(str(config.get("models", {}).get("device", "auto")), torch, "qrealign")
    try:
        processor = transformers.AutoProcessor.from_pretrained(
            str(directory), local_files_only=True, trust_remote_code=False
        )
        model = transformers.AutoModelForImageTextToText.from_pretrained(
            str(directory), local_files_only=True, use_safetensors=True, trust_remote_code=False
        )
        model.to(device=device, dtype=torch.float16 if device == "cuda" else torch.float32).eval()
        processor.tokenizer.padding_side = "left"  # the last position must be the prompt's last token
        level_ids = [processor.tokenizer.encode(word, add_special_tokens=False)[0] for word in LEVEL_WORDS]
    except Exception as exc:  # noqa: BLE001 - any loader failure means: unknown values, no invented score
        raise ModelUnavailable(
            "qrealign", "load_failed", f"Q-ReAlign could not be loaded ({exc.__class__.__name__})"
        ) from exc
    if len(set(level_ids)) != len(LEVEL_WORDS):
        raise ModelUnavailable("qrealign", "load_failed", "the rating words do not map to distinct tokens")
    return QRealignScorer(torch, model, processor, level_ids, device, directory.name)


# Loads SigLIP 2 from its verified local directory onto the configured device, in half precision on cuda.
# Raises ModelUnavailable for any missing, unsafe or unusable part; nothing is downloaded.
def load_siglip2(config: dict[str, Any]) -> SigLIP2Embedder:
    directory = locate_model(config, "siglip2")
    torch, transformers = _import_stack("siglip2")
    device = resolve_device(str(config.get("models", {}).get("device", "auto")), torch, "siglip2")
    try:
        processor = transformers.AutoProcessor.from_pretrained(
            str(directory), local_files_only=True, trust_remote_code=False
        )
        model = transformers.AutoModel.from_pretrained(
            str(directory), local_files_only=True, use_safetensors=True, trust_remote_code=False
        )
        model.to(device=device, dtype=torch.float16 if device == "cuda" else torch.float32).eval()
    except Exception as exc:  # noqa: BLE001 - any loader failure means: unknown values, no invented embedding
        raise ModelUnavailable(
            "siglip2", "load_failed", f"SigLIP 2 could not be loaded ({exc.__class__.__name__})"
        ) from exc
    return SigLIP2Embedder(torch, model, processor, device, directory.name)
