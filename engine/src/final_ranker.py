"""Final ranking: combine technical, face, and AI signals into a final score.

Also assigns a category and best selection slot, pre-ranks candidates for the AI stage,
and builds the balanced six-photo final set.
"""

from datetime import datetime
from typing import Any

from .utils import Photo


# Maps a 0-100 internal score to the 0-10 display scale.
def _to_display(value_100: float) -> float:
    return max(0.0, min(10.0, value_100 / 10.0))


# Computes a recency score (0-100): more recent photos score higher.
def _recency_score(photo: Photo) -> float:
    if not photo.modified_at:
        return 50.0
    try:
        modified = datetime.fromisoformat(photo.modified_at)
    except ValueError:
        return 50.0
    age_days = (datetime.now(tz=modified.tzinfo) - modified).days
    if age_days <= 365:
        return 100.0
    if age_days >= 365 * 5:
        return 30.0
    return float(100.0 - (age_days - 365) / (365 * 4) * 70.0)


# Derives a heuristic category when no AI judgment is available.
def _heuristic_category(photo: Photo) -> str:
    if photo.status == "rejected" or not photo.face_detected:
        return "REJECT"
    if photo.number_of_faces >= 2:
        return "SOCIAL"
    if photo.face_size_score >= 55:
        return "HEADSHOT"
    if photo.face_size_score <= 25:
        return "FULL_BODY"
    return "CANDID"


# Maps a category to a default selection slot.
def _slot_for_category(category: str) -> str:
    return {
        "HEADSHOT": "main_photo",
        "FULL_BODY": "full_body",
        "ACTIVITY": "activity_photo",
        "SOCIAL": "social_photo",
        "CANDID": "second_photo",
        "ELEGANT": "elegant_photo",
        "BAD": "reject",
        "REJECT": "reject",
    }.get(category, "backup")


# Computes the final score and assigns category/slot for one photo.
def rank_photo(photo: Photo, config: dict[str, Any]) -> Photo:
    weights = config["ranking"]
    gemma = photo.gemma or {}

    technical = photo.technical_score
    overall = float(gemma.get("overall_score", 0)) * 10 if gemma else technical
    face_clarity = photo.face_clarity_score
    aesthetic = photo.aesthetic_score
    naturalness = float(gemma.get("naturalness_score", 6)) * 10
    style_context = (
        (float(gemma.get("style_score", 6)) + float(gemma.get("context_score", 6))) / 2 * 10
    )
    diversity = 60.0  # refined by apply_diversity_rerank using clusters
    recency = _recency_score(photo)

    base = (
        technical * weights["technical_weight"]
        + overall * weights["gemma_overall_weight"]
        + face_clarity * weights["face_clarity_weight"]
        + aesthetic * weights.get("aesthetic_weight", 0.0)
        + naturalness * weights["naturalness_weight"]
        + style_context * weights["style_context_weight"]
        + diversity * weights["diversity_weight"]
        + recency * weights["recency_weight"]
    )

    base += _bonuses_and_penalties(photo, gemma)

    photo.final_score = _to_display(base)
    photo.category = str(gemma.get("category", _heuristic_category(photo)))
    photo.best_slot = str(gemma.get("best_slot", _slot_for_category(photo.category)))

    if gemma:
        photo.reason = str(gemma.get("reason", ""))
        photo.strengths = list(gemma.get("strengths", []))
        photo.weaknesses = list(gemma.get("weaknesses", []))

    if photo.status != "rejected":
        photo.status = "accepted"
    return photo


# Applies bonus and penalty points (0-100 scale) from available signals.
def _bonuses_and_penalties(photo: Photo, gemma: dict[str, Any]) -> float:
    delta = 0.0
    weaknesses = " ".join(gemma.get("weaknesses", [])).lower()

    if photo.face_clarity_score < 40 and photo.face_detected:
        delta -= 25
    if photo.number_of_faces > 4:
        delta -= 20
    if "sunglass" in weaknesses or "occhiali" in weaknesses:
        delta -= 10
    if "posed" in weaknesses or "posata" in weaknesses or "serious" in weaknesses:
        delta -= 15

    if photo.face_clarity_score > 70 and 90 < photo.brightness_score:
        delta += 15
    if float(gemma.get("naturalness_score", 0)) >= 8:
        delta += 10
    if float(gemma.get("style_score", 0)) >= 8:
        delta += 10

    return delta


# Applies a diversity penalty so near-duplicate clusters do not dominate the ranking.
# The strongest photo in each cluster keeps its score; weaker cluster-mates are damped.
def apply_diversity_rerank(photos: list[Photo], config: dict[str, Any]) -> None:
    weight = float(config["ranking"].get("diversity_weight", 0.1))
    ordered = sorted(
        (p for p in photos if p.status != "rejected"),
        key=lambda p: p.final_score,
        reverse=True,
    )
    seen: dict[int, int] = {}
    for photo in ordered:
        if photo.cluster_id < 0:
            continue
        rank_in_cluster = seen.get(photo.cluster_id, 0)
        if rank_in_cluster > 0:
            # Each additional cluster-mate is penalized progressively (capped).
            penalty = min(weight * rank_in_cluster, 0.6)
            photo.final_score = round(photo.final_score * (1.0 - penalty), 2)
        seen[photo.cluster_id] = rank_in_cluster + 1


# Flags local/Gemma/OpenAI disagreement as needs_manual_review instead of auto-discarding.
# A photo strong locally but weak per OpenAI (or vice versa) may still have real potential,
# so it is surfaced for a human rather than silently dropped. Scores are on the 0-10 scale.
def tag_conflicts(photos: list[Photo], high: float = 7.0, low: float = 4.0) -> int:
    tagged = 0
    for photo in photos:
        if not photo.eligible_for_openai:
            continue
        openai_score = photo.openai_premium_score or photo.openai_quality_score or photo.openai_broad_score
        if not openai_score:
            continue  # no OpenAI verdict yet -> nothing to disagree with
        local = float(photo.final_score)
        gemma = float((photo.gemma or {}).get("overall_score", 0)) if photo.gemma else None
        conflict = (
            (local >= high and openai_score <= low)
            or (local <= low and openai_score >= high)
            or (gemma is not None and gemma >= high and openai_score <= low)
            or (gemma is not None and openai_score >= high and gemma <= low)
        )
        if conflict and not photo.needs_manual_review:
            photo.needs_manual_review = True
            tagged += 1
    return tagged


# Selects the best candidates to forward to the AI stage, balancing technical/face/aesthetic.
def prerank_candidates(photos: list[Photo], count: int) -> list[Photo]:
    eligible = [p for p in photos if p.status != "rejected" and p.face_detected]
    eligible.sort(
        key=lambda p: (
            p.technical_score * 0.4
            + p.face_quality_score * 0.4
            + p.aesthetic_score * 0.2
        ),
        reverse=True,
    )
    return eligible[:count]


# Builds the balanced six-photo final set, preferring slot diversity over raw score.
def select_final_set(photos: list[Photo]) -> list[Photo]:
    ordered = sorted(
        (p for p in photos if p.status != "rejected"),
        key=lambda p: p.final_score,
        reverse=True,
    )
    slots = [
        "main_photo",
        "full_body",
        "activity_photo",
        "second_photo",
        "social_photo",
        "elegant_photo",
    ]
    chosen: list[Photo] = []
    used: set[str] = set()

    for slot in slots:
        match = next((p for p in ordered if p.best_slot == slot and p.id not in used), None)
        if match is None:
            match = next((p for p in ordered if p.id not in used), None)
        if match is not None:
            match.status = "selected"
            used.add(match.id)
            chosen.append(match)
    return chosen
