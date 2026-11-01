"""Local Ollama implementation of the VlmClient interface.

Sends only individual candidate photos (never the whole collection) to a local Ollama
endpoint, re-encoding each image to a bounded JPEG. Responses are validated as strict JSON
and retried a bounded number of times. All processing stays on the machine.
"""

import base64
import io
from typing import Any

import requests
from PIL import Image

from .. import progress_events as events
from ..utils import Photo, ensure_dir, mask_path, working_dir
from .base import VlmClient, VlmHealth
from .schema import PROMPT, RESPONSE_SCHEMA, parse_review


# Classifies why a response failed parsing, for privacy-safe diagnostics. The reason code
# alone (empty / no JSON / schema mismatch) is enough to triage; the raw model text is only
# appended when debug_raw_ai_response is enabled, matching the project's privacy posture.
def _failure_reason(raw: str, debug: bool, limit: int = 160) -> str:
    text = (raw or "").strip()
    if not text:
        reason = "empty response"
    elif "{" not in text or "}" not in text:
        reason = "no JSON object in response"
    else:
        reason = "JSON did not match the required schema"
    if debug:
        reason += f" | raw: {' '.join(text.split())[:limit]}"
    return reason


class OllamaVlmClient(VlmClient):
    """Talks to a local Ollama server's /api/generate endpoint."""

    # Stores connection and behavior settings for a single model.
    def __init__(self, config: dict[str, Any], model: str) -> None:
        self._config = config
        self.model = model
        self._url = config["model"]["ollama_url"].rstrip("/")
        self._timeout = int(config["model"].get("request_timeout_seconds", 120))
        self._retries = int(config["model"].get("max_retries", 2))
        self._num_predict = int(config["model"].get("num_predict", 768))
        self._debug = bool(config["privacy"].get("debug_raw_ai_response", False))

    # Probes reachability of the runtime and presence of this client's model.
    def health(self) -> VlmHealth:
        try:
            response = requests.get(f"{self._url}/api/tags", timeout=5)
            response.raise_for_status()
        except requests.RequestException as exc:
            return VlmHealth(
                False, False,
                f"Ollama is not reachable at {self._url} ({exc}). Start it with 'ollama serve'.",
            )
        names = [m.get("name", "") for m in response.json().get("models", [])]
        if not any(self.model in n for n in names):
            return VlmHealth(
                True, False, f"Model '{self.model}' is not installed. Run: ollama pull {self.model}"
            )
        return VlmHealth(True, True, "ok")

    # Sends one photo and returns a validated review, retrying on transient failures.
    def review(self, photo: Photo) -> dict[str, Any] | None:
        payload = {
            "model": self.model,
            "prompt": PROMPT,
            "images": [self._encode_image(photo.file_path)],
            "stream": False,
            # Constrain output to the exact review schema (Ollama structured outputs) instead
            # of plain "json"; thinking/community models otherwise drift from the shape.
            "format": RESPONSE_SCHEMA,
            # This model family is thinking-capable; reasoning tokens corrupt and truncate the
            # JSON, so we disable them and only ask for the verdict.
            "think": False,
            "options": {"temperature": 0.2, "num_predict": self._num_predict},
        }
        for attempt in range(self._retries + 1):
            try:
                response = requests.post(
                    f"{self._url}/api/generate", json=payload, timeout=self._timeout
                )
                response.raise_for_status()
                raw = response.json().get("response", "")
                if self._debug:
                    self._save_debug(photo.id, raw)
                review = parse_review(raw)
                if review is not None:
                    review["_model"] = self.model
                    return review
                events.log(
                    f"Invalid JSON from {self.model} for {photo.id} "
                    f"(attempt {attempt + 1}/{self._retries + 1}): "
                    f"{_failure_reason(raw, self._debug)}",
                    "warning",
                )
            except requests.RequestException as exc:
                events.log(
                    f"Ollama request failed for {mask_path(photo.file_path, self._config)}: {exc}",
                    "warning",
                )
        return None

    # Re-encodes any image (including HEIC) to a bounded JPEG and returns base64.
    def _encode_image(self, path: str, max_dim: int = 1280) -> str:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((max_dim, max_dim))
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    # Writes a raw model response to the working debug folder (debug mode only).
    def _save_debug(self, photo_id: str, raw: str) -> None:
        debug_dir = ensure_dir(working_dir(self._config) / "debug")
        (debug_dir / f"{photo_id}.json").write_text(raw, encoding="utf-8")
