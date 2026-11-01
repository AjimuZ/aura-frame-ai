"""Backwards-compatible facade over the local VLM package (src.vlm).

The judging logic now lives in src.vlm (provider-agnostic interface, strict JSON schema,
primary + fallback model, health checks). This module preserves the original public
functions so existing callers keep working.
"""

from typing import Any

from .utils import Photo
from .vlm import VlmRunner
from .vlm import judge_candidates as _judge_candidates
from .vlm.schema import PROMPT, parse_review  # noqa: F401 - re-exported for compatibility


# Verifies the configured model is reachable and installed (primary or fallback).
def check_availability(config: dict[str, Any]) -> tuple[bool, str]:
    report = VlmRunner(config).check()
    available = bool(report["primaryAvailable"] or report["fallbackAvailable"])
    return available, "ok" if available else report["message"]


# Judges a single photo using the runner (primary + fallback).
def judge_photo(photo: Photo, config: dict[str, Any]) -> dict[str, Any] | None:
    return VlmRunner(config).review(photo)


# Judges a list of candidate photos, attaching reviews and emitting progress.
def judge_candidates(candidates: list[Photo], config: dict[str, Any]) -> None:
    _judge_candidates(candidates, config)
