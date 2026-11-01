"""Stage orchestration for OpenAI Quality Mode.

For each single-image stage (broad_scan, quality_review, premium_review) this selects
candidates by priority/previous-stage score, serves cache hits for free, reserves budget,
sends the rest with bounded concurrency + retries, parses + persists results incrementally,
and emits progress. The comparative Final Judge sends the top candidates together and
returns a Top 6 / Top 20 / per-slot selection.
"""

from typing import Any, Callable

from .. import progress_events as events
from ..utils import Photo, working_dir
from . import openai_cost_estimator as costs
from .openai_budget_guard import BudgetGuard
from .openai_cache import append_cache, cache_key, load_cache
from .openai_client import FatalApiError, ModelNotAvailableError, OpenAiClient
from .openai_image_preparer import data_url, prepare_image
from .openai_prompts import PROMPT_VERSIONS, build_final_judge_prompt, prompt_for
from .openai_rate_limiter import RateLimiter
from .openai_result_parser import parse

_SINGLE_IMAGE_STAGES = ("broad_scan", "quality_review", "premium_review")


# Returns True only when the user has explicitly enabled cloud review (both switches).
def is_cloud_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("privacy", {}).get("allow_cloud_api", False)) and bool(
        config.get("openai", {}).get("enabled", False)
    )


# Returns (detail, max_px, jpeg_quality) for a stage's image preparation.
def _image_settings(stage: str, config: dict[str, Any]) -> tuple[str, int, int]:
    images = config.get("openai", {}).get("images", {})
    detail, max_px = costs.stage_image_params(stage, config)
    jpeg_q = int(images.get(f"jpeg_quality_{stage}", 85))
    return detail, max_px, jpeg_q


# Selects and orders the candidate photos for a stage, capped by openai.limits.
def select_candidates(photos: list[Photo], stage: str, config: dict[str, Any]) -> list[Photo]:
    limits = config.get("openai", {}).get("limits", {})
    if stage == "broad_scan":
        pool = [p for p in photos if p.eligible_for_openai]
        pool.sort(key=lambda p: (p.openai_priority or 9, -p.technical_score))
        cap = int(limits.get("broad_scan_max_photos", 4000))
    elif stage == "quality_review":
        pool = [p for p in photos if p.openai_broad and not (p.openai_broad or {}).get("reject")]
        pool.sort(key=lambda p: p.openai_broad_score, reverse=True)
        cap = int(limits.get("quality_review_max_photos", 1000))
    elif stage == "premium_review":
        pool = [p for p in photos if p.openai_quality and (p.openai_quality or {}).get("keep", True)]
        pool.sort(key=lambda p: p.openai_quality_score, reverse=True)
        cap = int(limits.get("premium_review_max_photos", 200))
    else:  # final_judge
        pool = [p for p in photos if p.eligible_for_openai and (p.openai_premium or p.openai_quality or p.openai_broad)]
        pool.sort(
            key=lambda p: (p.openai_premium_score, p.openai_quality_score, p.openai_broad_score),
            reverse=True,
        )
        cap = int(limits.get("final_judge_max_photos", 40))
    return pool[:cap]


# Applies a parsed single-image result onto the photo and accumulates its estimated cost.
def _apply_result(stage: str, photo: Photo, parsed: dict[str, Any], cost: float) -> None:
    photo.estimated_cost_usd = round(photo.estimated_cost_usd + cost, 6)
    if stage == "broad_scan":
        photo.openai_broad = parsed
        photo.openai_broad_score = parsed.get("score", 0.0)
        photo.best_slot = parsed.get("best_slot", photo.best_slot)
        photo.openai_reason = parsed.get("reason", photo.openai_reason)
    elif stage == "quality_review":
        photo.openai_quality = parsed
        photo.openai_quality_score = parsed.get("score", 0.0)
        photo.best_slot = parsed.get("best_slot", photo.best_slot)
        photo.openai_reason = parsed.get("reason", photo.openai_reason)
        photo.openai_risk = parsed.get("risk", photo.openai_risk)
    elif stage == "premium_review":
        photo.openai_premium = parsed
        photo.openai_premium_score = parsed.get("score", 0.0)
        photo.best_slot = parsed.get("best_slot", photo.best_slot)
        photo.openai_reason = parsed.get("reason", photo.openai_reason)
        photo.openai_risk = parsed.get("risk", photo.openai_risk)


# Returns the score to surface in the openai_photo_scored event for a stage.
def _stage_score(stage: str, photo: Photo) -> float:
    return {
        "broad_scan": photo.openai_broad_score,
        "quality_review": photo.openai_quality_score,
        "premium_review": photo.openai_premium_score,
    }.get(stage, 0.0)


# Runs one single-image stage end-to-end. Mutates photos in place; calls persist() after each
# chunk for incremental durability. Returns a small summary dict.
def run_stage(
    stage: str,
    photos: list[Photo],
    config: dict[str, Any],
    *,
    budget: BudgetGuard,
    client: OpenAiClient | None = None,
    persist: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if stage not in _SINGLE_IMAGE_STAGES:
        raise ValueError(f"run_stage does not handle stage '{stage}'")
    client = client or OpenAiClient(config)
    model = client.resolve_model(stage)  # raises ModelNotAvailableError if neither is available
    prompt = prompt_for(stage)
    prompt_version = PROMPT_VERSIONS[stage]
    detail, max_px, jpeg_q = _image_settings(stage, config)
    work = working_dir(config)
    cache = load_cache(config)

    candidates = select_candidates(photos, stage, config)
    total = len(candidates)
    processed = 0
    to_send: list[tuple[Photo, float]] = []

    # Serve cache hits for free; reserve budget for the rest until the hard stop is reached.
    for photo in candidates:
        hit = cache.get(cache_key(photo.file_hash, stage, model, prompt_version))
        if hit:
            _apply_result(stage, photo, hit["result_json"], 0.0)
            processed += 1
            continue
        cost = costs.estimate_request_cost(model, stage, photo.width or max_px, photo.height or max_px, config)
        if not budget.reserve(cost):
            break
        to_send.append((photo, cost))

    # One request worker (runs under the rate limiter's concurrency + retry control).
    def worker(pair: tuple[Photo, float]) -> dict[str, Any] | None:
        photo, _cost = pair
        prepared = prepare_image(photo.file_path, max_side=max_px, jpeg_quality=jpeg_q, work_dir=work)
        raw = client.review_image(
            model=model,
            prompt=prompt,
            image_data_url=data_url(prepared),
            detail=detail,
            max_output_tokens=costs.assumed_output_tokens(stage, config) + 64,
        )
        return parse(stage, raw)

    limiter = RateLimiter(config)
    chunk = max(20, limiter.max_parallel * 5)
    for start in range(0, len(to_send), chunk):
        batch = to_send[start : start + chunk]
        results = limiter.run(batch, worker)
        for (photo, cost), parsed in zip(batch, results):
            if parsed is None:
                photo.failed_openai_review = True
            else:
                _apply_result(stage, photo, parsed, cost)
                if config.get("openai", {}).get("output", {}).get("save_openai_scores", True):
                    append_cache(
                        config,
                        photo_hash=photo.file_hash,
                        stage=stage,
                        model=model,
                        prompt_version=prompt_version,
                        result_json=parsed,
                        estimated_cost_usd=cost,
                    )
            processed += 1
            events.openai_photo_scored(photo.id, stage, _stage_score(stage, photo), photo.best_slot)
        if persist:
            persist()
        events.openai_progress(stage, processed, total, budget.spent_usd, budget.remaining_usd)

    events.openai_progress(stage, processed, total, budget.spent_usd, budget.remaining_usd, f"{stage} complete")
    return {"stage": stage, "processed": processed, "total": total, "model": model, "spent_usd": budget.spent_usd}


# Builds the anonymized metadata block for one candidate (no names/paths/EXIF).
def _judge_meta(photo: Photo) -> dict[str, Any]:
    gemma = photo.gemma or {}
    return {
        "photo_id": photo.id,
        "local_score": round(photo.final_score, 2),
        "gemma_score": gemma.get("overall_score", None),
        "openai_broad_score": round(photo.openai_broad_score, 2),
        "openai_quality_score": round(photo.openai_quality_score, 2),
        "openai_premium_score": round(photo.openai_premium_score, 2),
        "category": photo.category,
        "best_slot": photo.best_slot,
        "diversity_cluster": photo.cluster_id,
    }


# Runs the comparative Final Judge over the top candidates. Reserves one combined request
# cost from the budget; returns the parsed selection dict or None (skipped/failed).
def run_final_judge(
    photos: list[Photo],
    config: dict[str, Any],
    *,
    budget: BudgetGuard,
    client: OpenAiClient | None = None,
) -> dict[str, Any] | None:
    client = client or OpenAiClient(config)
    model = client.resolve_model("final_judge")
    detail, max_px, jpeg_q = _image_settings("final_judge", config)
    work = working_dir(config)
    candidates = select_candidates(photos, "final_judge", config)
    if not candidates:
        events.log("Final Judge skipped: no candidates available.", "warning")
        return None

    # Estimate the single combined request (all images + metadata + a larger JSON answer).
    input_per_1m, output_per_1m = costs.pricing_for(model, config)
    image_tokens = sum(
        costs.estimate_image_tokens(detail, p.width or max_px, p.height or max_px) for p in candidates
    )
    output_tokens = costs.assumed_output_tokens("final_judge", config)
    est_cost = (image_tokens + costs._prompt_tokens("final_judge")) / 1_000_000 * input_per_1m + (
        output_tokens / 1_000_000
    ) * output_per_1m
    if not budget.reserve(est_cost):
        events.log("Final Judge skipped: budget exhausted.", "warning")
        return None

    events.openai_progress("final_judge", 0, len(candidates), budget.spent_usd, budget.remaining_usd)
    image_urls: list[str] = []
    meta: list[dict[str, Any]] = []
    for photo in candidates:
        try:
            prepared = prepare_image(photo.file_path, max_side=max_px, jpeg_quality=jpeg_q, work_dir=work)
        except Exception as exc:  # noqa: BLE001 - skip an unreadable candidate, keep the rest
            events.log(f"Final Judge: skipping unreadable candidate {photo.id}: {exc}", "warning")
            continue
        image_urls.append(data_url(prepared))
        meta.append(_judge_meta(photo))

    prompt = build_final_judge_prompt(meta)
    limiter = RateLimiter(config)
    try:
        raw = limiter.call_with_retries(
            lambda: client.review_many(
                model=model,
                prompt=prompt,
                image_data_urls=image_urls,
                detail=detail,
                max_output_tokens=output_tokens + 256,
            )
        )
    except (FatalApiError, ModelNotAvailableError) as exc:
        events.log(f"Final Judge failed: {exc}", "error")
        return None
    result = parse("final_judge", raw)
    events.openai_progress(
        "final_judge", len(candidates), len(candidates), budget.spent_usd, budget.remaining_usd, "final_judge complete"
    )
    return result
