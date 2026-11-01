"""Cost estimation for OpenAI Quality Mode.

Images are billed as tokens. This module approximates per-request cost from the image
detail level, the resized dimensions, and a configurable per-model price table
(openai.pricing in config.yaml). The numbers are ESTIMATES used to drive the budget guard
and the pre-run estimate shown in the UI; verify against live OpenAI pricing before relying
on them. Unknown models fall back to openai.pricing.default_pricing.
"""

import math
from typing import Any

from . import openai_prompts

# OpenAI tokenizes a "low" detail image as a small flat cost, and a "high" detail image as a
# base plus one block per 512px tile (after the documented down-scaling). These constants
# match the widely-documented gpt-4o-class image tokenization and are good enough for budgeting.
_LOW_DETAIL_TOKENS = 85
_HIGH_BASE_TOKENS = 85
_HIGH_TILE_TOKENS = 170


# Approximates the number of input tokens an image contributes for a given detail level.
def estimate_image_tokens(detail: str, width: int, height: int) -> int:
    if detail == "low" or width <= 0 or height <= 0:
        return _LOW_DETAIL_TOKENS
    # Down-scale to fit 2048x2048, then scale the shortest side to 768 (OpenAI's rule).
    w, h = float(width), float(height)
    if max(w, h) > 2048:
        scale = 2048 / max(w, h)
        w, h = w * scale, h * scale
    if min(w, h) > 768:
        scale = 768 / min(w, h)
        w, h = w * scale, h * scale
    tiles = math.ceil(w / 512) * math.ceil(h / 512)
    return _HIGH_BASE_TOKENS + _HIGH_TILE_TOKENS * max(tiles, 1)


# Returns (input_per_1m, output_per_1m) USD for a model, falling back to default_pricing.
def pricing_for(model: str, config: dict[str, Any]) -> tuple[float, float]:
    pricing = config.get("openai", {}).get("pricing", {})
    default = pricing.get("default_pricing", {"input_per_1m": 2.0, "output_per_1m": 8.0})
    entry = pricing.get("models", {}).get(model, default)
    return float(entry.get("input_per_1m", default["input_per_1m"])), float(
        entry.get("output_per_1m", default["output_per_1m"])
    )


# Returns the assumed completion-token count for a stage (strict JSON verdicts are small).
def assumed_output_tokens(stage: str, config: dict[str, Any]) -> int:
    table = config.get("openai", {}).get("pricing", {}).get("assumed_output_tokens", {})
    return int(table.get(stage, 200))


# Rough text-token estimate (~4 chars/token) for a prompt string.
def _prompt_tokens(stage: str) -> int:
    if stage == "final_judge":
        return len(openai_prompts._FINAL_JUDGE_INSTRUCTIONS) // 4
    return len(openai_prompts.prompt_for(stage)) // 4


# Reads the configured detail level and max pixel bound for a stage.
def stage_image_params(stage: str, config: dict[str, Any]) -> tuple[str, int]:
    images = config.get("openai", {}).get("images", {})
    detail = str(images.get(f"{stage}_detail", "high"))
    max_px = int(images.get(f"{stage}_max_px", 1024))
    return detail, max_px


# Estimates the USD cost of a single request given the model, stage, and resized dimensions.
def estimate_request_cost(
    model: str, stage: str, width: int, height: int, config: dict[str, Any]
) -> float:
    detail, _ = stage_image_params(stage, config)
    input_tokens = estimate_image_tokens(detail, width, height) + _prompt_tokens(stage)
    output_tokens = assumed_output_tokens(stage, config)
    input_per_1m, output_per_1m = pricing_for(model, config)
    return (input_tokens / 1_000_000) * input_per_1m + (output_tokens / 1_000_000) * output_per_1m


# Estimates the cost of running a whole stage over `count` photos, using the stage's nominal
# max dimensions as a worst-case square (the real per-photo cost is computed at send time).
def estimate_stage_cost(stage: str, count: int, config: dict[str, Any]) -> float:
    model = config.get("openai", {}).get("models", {}).get(stage, "")
    detail, max_px = stage_image_params(stage, config)
    per = estimate_request_cost(model, stage, max_px, max_px, config)
    return per * max(count, 0)
