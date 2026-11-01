"""Consensus ranking across local + OpenAI signals.

The local gate is intentionally minimal, so the local score is treated as metadata with a
small weight; OpenAI is the main judge of dating value. This module computes a consensus
score, applies bonuses/penalties, integrates the comparative Final Judge selection, and
assigns the Top 6 / 20 / 50 / 100 tiers. Top 50/100 come from consensus ordering (the Final
Judge only sees ~40 candidates and decides Top 6 / Top 20 / per-slot).
"""

from typing import Any

from ..utils import Photo

# Canonical six-slot order (second_photo is the candid slot, exported as 04_candid).
SLOT_ORDER = [
    "main_photo", "full_body", "activity_photo", "second_photo", "social_photo", "elegant_photo",
]


# Returns the eligible (non hard-rejected, non failed) photos for ranking.
def _eligible(photos: list[Photo]) -> list[Photo]:
    return [p for p in photos if p.eligible_for_openai and p.gate_status not in {"hard_reject", "failed_read"}]


# Computes a 0-10 diversity score per photo: rarer visual clusters score higher so the set
# does not collapse onto near-identical shots.
def _diversity_scores(photos: list[Photo]) -> dict[str, float]:
    sizes: dict[int, int] = {}
    for p in photos:
        if p.cluster_id >= 0:
            sizes[p.cluster_id] = sizes.get(p.cluster_id, 0) + 1
    out: dict[str, float] = {}
    for p in photos:
        if p.cluster_id < 0:
            out[p.id] = 8.0  # unclustered/unique-ish
        else:
            size = sizes.get(p.cluster_id, 1)
            out[p.id] = max(2.0, 10.0 - (size - 1) * 2.0)
    return out


# Returns the effective per-stage scores (0-10), gracefully filling a missing later-stage
# score from the previous stage so photos that didn't reach a stage are not double-penalized.
def _effective_scores(photo: Photo) -> tuple[float, float, float, float]:
    local = float(photo.final_score)  # already 0-10
    broad = float(photo.openai_broad_score)
    quality = float(photo.openai_quality_score) or broad
    premium = float(photo.openai_premium_score) or quality
    return local, broad, quality, premium


# Applies small bonus/penalty adjustments (0-10 scale) from the available signals.
def _bonuses_and_penalties(photo: Photo) -> float:
    delta = 0.0
    risk = (photo.openai_risk or "").lower()
    reason = (photo.openai_reason or "").lower()
    # Penalties
    if photo.is_near_duplicate:
        delta -= 0.5
    if photo.number_of_faces > 4:
        delta -= 0.5
    if photo.face_detected and photo.face_clarity_score and photo.face_clarity_score < 40:
        delta -= 0.5
    if "local_flag_low_local_score" in photo.soft_flags:
        delta -= 0.3
    if "local_flag_old_photo" in photo.soft_flags:
        delta -= 0.2
    if photo.best_slot in {"reject", "backup"}:
        delta -= 0.4
    if any(word in risk for word in ("cringe", "posed", "posata", "linkedin")):
        delta -= 0.4
    # Bonuses
    if photo.face_clarity_score and photo.face_clarity_score > 70:
        delta += 0.3
    if photo.face_detected and not photo.group_photo:
        delta += 0.1
    if any(word in reason for word in ("natural", "naturale", "authentic", "autentic")):
        delta += 0.2
    return delta


# Computes consensus_score for every eligible photo (others are set to 0).
def compute_consensus_scores(photos: list[Photo], config: dict[str, Any]) -> None:
    weights = config.get("openai", {}).get("consensus_weights", {})
    w_local = float(weights.get("local", 0.10))
    w_broad = float(weights.get("broad", 0.25))
    w_quality = float(weights.get("quality", 0.30))
    w_premium = float(weights.get("premium", 0.25))
    w_diversity = float(weights.get("diversity", 0.10))

    diversity = _diversity_scores(photos)
    eligible_ids = {p.id for p in _eligible(photos)}
    for photo in photos:
        if photo.id not in eligible_ids:
            photo.consensus_score = 0.0
            continue
        local, broad, quality, premium = _effective_scores(photo)
        base = (
            w_local * local
            + w_broad * broad
            + w_quality * quality
            + w_premium * premium
            + w_diversity * diversity.get(photo.id, 6.0)
        )
        photo.consensus_score = round(max(0.0, min(10.0, base + _bonuses_and_penalties(photo))), 3)


# Integrates the Final Judge selection into per-photo flags (Top 6, slot, judge score, notes).
def integrate_final_judge(photos: list[Photo], judge_result: dict[str, Any]) -> None:
    by_id = {p.id: p for p in photos}
    for entry in judge_result.get("top_6", []):
        photo = by_id.get(entry.get("photo_id", ""))
        if not photo:
            continue
        photo.in_top_6 = True
        photo.in_top_20 = True
        photo.final_selected_slot = entry.get("slot", photo.best_slot)
        photo.final_judge_score = 10.0
        if entry.get("reason"):
            photo.openai_reason = entry["reason"]
        if entry.get("risk"):
            photo.openai_risk = entry["risk"]
    for pid in judge_result.get("top_20", []):
        photo = by_id.get(pid)
        if photo and not photo.in_top_6:
            photo.in_top_20 = True
            photo.final_judge_score = max(photo.final_judge_score, 7.0)
    for slot, ids in judge_result.get("slot_candidates", {}).items():
        for pid in ids:
            photo = by_id.get(pid)
            if photo:
                photo.final_judge_score = max(photo.final_judge_score, 5.0)
                if not photo.final_selected_slot:
                    photo.final_selected_slot = slot


# Assigns Top 20/50/100 tiers from consensus ordering (Top 6 / judge Top 20 already applied),
# and returns the ordered Top 6 final set. Mutates in_top_* flags in place.
def assign_tiers(photos: list[Photo], config: dict[str, Any]) -> list[Photo]:
    ordered = sorted(_eligible(photos), key=lambda p: p.consensus_score, reverse=True)
    for rank, photo in enumerate(ordered):
        if rank < 100:
            photo.in_top_100 = True
        if rank < 50:
            photo.in_top_50 = True
        if rank < 20:
            photo.in_top_20 = True
    return build_final_six(photos, ordered)


# Builds the ordered six-photo set: prefer Final-Judge picks per slot, then fill remaining
# slots with the highest-consensus unused photo whose best_slot matches, then any unused.
def build_final_six(photos: list[Photo], ordered: list[Photo]) -> list[Photo]:
    by_slot_judge: dict[str, Photo] = {}
    for photo in photos:
        if photo.in_top_6 and photo.final_selected_slot:
            by_slot_judge.setdefault(photo.final_selected_slot, photo)

    chosen: list[Photo] = []
    used: set[str] = set()
    for slot in SLOT_ORDER:
        pick = by_slot_judge.get(slot)
        if pick is None:
            pick = next((p for p in ordered if p.best_slot == slot and p.id not in used), None)
        if pick is None:
            pick = next((p for p in ordered if p.id not in used), None)
        if pick is not None:
            pick.in_top_6 = True
            pick.in_top_20 = True
            if not pick.final_selected_slot:
                pick.final_selected_slot = slot
            used.add(pick.id)
            chosen.append(pick)
    return chosen


# Orchestrates consensus scoring + (optional) Final Judge integration + tier assignment.
# Returns the ordered Top 6 final set.
def rank_for_openai(
    photos: list[Photo], config: dict[str, Any], judge_result: dict[str, Any] | None = None
) -> list[Photo]:
    compute_consensus_scores(photos, config)
    if judge_result:
        integrate_final_judge(photos, judge_result)
    return assign_tiers(photos, config)
