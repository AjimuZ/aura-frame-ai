"""VLM runner: coordinates a primary and a fallback local model.

Health-checks the primary model; if its runtime is unreachable or its model is missing it
transparently falls back to the configured fallback model. If neither is available the
pipeline continues using the technical/face/aesthetic ranking only.
"""

from typing import Any

from .. import progress_events as events
from ..utils import Photo
from .base import VlmClient, VlmHealth
from .ollama_client import OllamaVlmClient


class VlmRunner:
    """Runs photo reviews against a primary model with a fallback model."""

    # Builds primary/fallback clients from config (provider currently 'ollama').
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        primary_name = config["model"]["name"]
        fallback_name = config["model"].get("fallback_name", "")
        self.primary: VlmClient = OllamaVlmClient(config, primary_name)
        self.fallback: VlmClient | None = (
            OllamaVlmClient(config, fallback_name)
            if fallback_name and fallback_name != primary_name
            else None
        )
        self._active: VlmClient | None = None

    # Probes runtime/model presence for primary and fallback; returns a combined report.
    def check(self) -> dict[str, Any]:
        primary = self.primary.health()
        report: dict[str, Any] = {
            "runtimeReachable": primary.runtime_reachable,
            "primaryModel": self.primary.model,
            "primaryAvailable": primary.model_available,
            "message": primary.message,
            "fallbackModel": self.fallback.model if self.fallback else None,
            "fallbackAvailable": False,
        }
        if self.fallback is not None:
            fb = self.fallback.health()
            report["fallbackAvailable"] = fb.model_available
            report["runtimeReachable"] = report["runtimeReachable"] or fb.runtime_reachable
        return report

    # Selects the first usable client (primary preferred), caching the choice.
    def _select(self) -> tuple[VlmClient | None, VlmHealth]:
        if self._active is not None:
            return self._active, VlmHealth(True, True, "ok")
        health = self.primary.health()
        if health.model_available:
            self._active = self.primary
            return self.primary, health
        if self.fallback is not None:
            fb = self.fallback.health()
            if fb.model_available:
                events.log(
                    f"Primary model unavailable; using fallback '{self.fallback.model}'.", "warning"
                )
                self._active = self.fallback
                return self.fallback, fb
        return None, health

    # Reviews one photo with the selected client (or None when no model is available).
    def review(self, photo: Photo) -> dict[str, Any] | None:
        client, _ = self._select()
        return client.review(photo) if client else None


# Judges candidate photos in place, attaching reviews and emitting progress.
def judge_candidates(candidates: list[Photo], config: dict[str, Any]) -> None:
    runner = VlmRunner(config)
    client, health = runner._select()
    if client is None:
        events.log(health.message, "error")
        events.log("Skipping AI review; using technical ranking only.", "warning")
        return

    total = len(candidates)
    for i, photo in enumerate(candidates, start=1):
        review = runner.review(photo)
        if review is not None:
            photo.gemma = review
        events.progress("gemma", i, total, f"AI review {i}/{total}")
