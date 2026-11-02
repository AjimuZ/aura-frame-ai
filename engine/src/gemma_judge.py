"""Backwards-compatible facade over the model-stage package (src.vlm).

The stage now checks the local vision provider (src.providers). This module preserves the
public names that existing callers use.
"""

from typing import Any

from .utils import Photo
from .vlm import VlmRunner
from .vlm import judge_candidates as _judge_candidates
from .vlm.schema import PROMPT, parse_review  # noqa: F401 - re-exported for compatibility


# Verifies the configured local vision provider is reachable and has a model loaded.
def check_availability(config: dict[str, Any]) -> tuple[bool, str]:
    runner = VlmRunner(config)
    try:
        health = runner.health()
    finally:
        runner.close()
    return health.ready, "ok" if health.ready else health.message


# Probes the provider for a list of candidate photos; the candidates are left unchanged.
def judge_candidates(candidates: list[Photo], config: dict[str, Any]) -> None:
    _judge_candidates(candidates, config)
