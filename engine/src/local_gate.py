"""Local Minimal Gate: privacy-first, deliberately permissive triage.

The gate's only job is to remove objectively unusable photos (corrupt/unreadable, exact
duplicate, fully black, fully white, extremely low resolution, extreme blur, clearly
non-photographic) and to attach soft flags to everything else. It does NOT judge dating
suitability: borderline photos (no face detected, small face, group, selfie, mildly dark
or blurry, odd composition, low local score) are KEPT and flagged so the optional OpenAI
review can decide. Rule of thumb: "when in doubt, keep it for OpenAI."

It runs after quality + face measurement and assigns each photo a gate_status, soft flags,
eligibility, and an OpenAI processing priority. Only hard_reject / failed_read are excluded
from OpenAI; everything else flows forward.
"""

from datetime import datetime, timezone
from typing import Any

from . import progress_events as events
from .utils import Photo

# Budget-guard processing order for eligible photos (lower number = processed first).
PRIORITY: dict[str, int] = {
    "openai_candidate": 1,
    "needs_manual_review": 2,
    "low_priority_openai_candidate": 3,
    "duplicate_candidate": 4,
}

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_width_hard_reject": 400,
    "min_height_hard_reject": 400,
    "extreme_blur_threshold": 35,
    "possible_blur_threshold": 120,
    "extreme_dark_brightness": 10,
    "possible_dark_brightness": 35,
    "extreme_bright_brightness": 245,
    "possible_bright_brightness": 230,
    "small_face_score": 25,
    "low_local_score": 40,
    "near_duplicate_hash_distance": 3,
}


# Returns the gate threshold map merged over safe defaults so missing keys never raise.
def _thresholds(config: dict[str, Any]) -> dict[str, float]:
    merged = dict(_DEFAULT_THRESHOLDS)
    merged.update(config.get("local_gate", {}).get("thresholds", {}) or {})
    return {k: float(v) for k, v in merged.items()}


# Records the final gate decision on the photo (status, eligibility, priority) and returns it.
def _decide(photo: Photo, gate_status: str, *, eligible: bool, hard_reason: str = "") -> Photo:
    photo.gate_status = gate_status
    photo.eligible_for_openai = eligible
    photo.openai_priority = PRIORITY.get(gate_status, 0)
    photo.hard_reject_reason = hard_reason
    if eligible and "local_flag_needs_openai_review" not in photo.soft_flags:
        photo.soft_flags.append("local_flag_needs_openai_review")
    return photo


# Returns an extreme-only hard-reject reason, or "" to keep the photo. Each rule can be
# disabled via local_gate.hard_reject in config.
def _extreme_hard_reject_reason(photo: Photo, th: dict[str, float], rules: dict[str, Any]) -> str:
    def on(name: str) -> bool:
        return bool(rules.get(name, True))

    if on("extremely_low_resolution") and photo.width and photo.height and (
        photo.width < th["min_width_hard_reject"] or photo.height < th["min_height_hard_reject"]
    ):
        return "extremely_low_resolution"
    if on("fully_black_image") and photo.brightness_mean < th["extreme_dark_brightness"]:
        return "fully_black_image"
    if on("fully_white_image") and photo.brightness_mean > th["extreme_bright_brightness"]:
        return "fully_white_image"
    if on("extreme_blur") and photo.laplacian_variance < th["extreme_blur_threshold"]:
        return "extreme_blur"
    if on("non_photographic_image") and photo.likely_screenshot:
        return "non_photographic_image"
    return ""


# Reproduces the legacy aggressive rejection thresholds for strict mode (parity with the
# pre-gate pipeline). Returns a reject reason or "".
def _strict_reject_reason(photo: Photo, config: dict[str, Any]) -> str:
    f = config.get("filters", {})
    blur_threshold = float(f.get("blur_threshold", 120))
    if photo.laplacian_variance < blur_threshold * 0.4:
        return "too_blurry"
    if photo.brightness_mean < float(f.get("min_brightness", 35)):
        return "too_dark"
    if photo.brightness_mean > float(f.get("max_brightness", 230)):
        return "too_bright"
    aspect = photo.width / photo.height if photo.height else 1.0
    if photo.width and photo.height and not (
        float(f.get("min_aspect_ratio", 0.4)) <= aspect <= float(f.get("max_aspect_ratio", 2.6))
    ):
        return "bad_aspect"
    if photo.likely_screenshot:
        return "likely_screenshot"
    return ""


# Appends a soft flag once (no duplicates), preserving insertion order.
def _flag(photo: Photo, name: str) -> None:
    if name not in photo.soft_flags:
        photo.soft_flags.append(name)


# Attaches non-blocking soft flags describing weak signals; never rejects.
def _attach_soft_flags(photo: Photo, th: dict[str, float], config: dict[str, Any]) -> None:
    if not photo.face_detected:
        _flag(photo, "local_flag_face_not_detected")
    elif photo.face_size_score and photo.face_size_score < th["small_face_score"]:
        _flag(photo, "local_flag_small_face")
    if photo.group_photo or photo.number_of_faces >= 2:
        _flag(photo, "local_flag_group_photo")
    if photo.brightness_mean and (
        photo.brightness_mean < th["possible_dark_brightness"]
        or photo.brightness_mean > th["possible_bright_brightness"]
    ):
        _flag(photo, "local_flag_low_light")
    if photo.laplacian_variance and photo.laplacian_variance < th["possible_blur_threshold"]:
        _flag(photo, "local_flag_possible_blur")
    f = config.get("filters", {})
    if photo.width and photo.height and (
        photo.width < int(f.get("min_width", 800)) or photo.height < int(f.get("min_height", 800))
    ):
        _flag(photo, "local_flag_low_resolution")
    aspect = photo.width / photo.height if photo.height else 1.0
    if photo.width and photo.height and not (
        float(f.get("min_aspect_ratio", 0.4)) <= aspect <= float(f.get("max_aspect_ratio", 2.6))
    ):
        _flag(photo, "local_flag_odd_composition")
    if photo.technical_score and photo.technical_score < th["low_local_score"]:
        _flag(photo, "local_flag_low_local_score")
    if _looks_old(photo):
        _flag(photo, "local_flag_old_photo")


# True when the photo's capture/modification time is more than ~5 years old (best effort).
def _looks_old(photo: Photo) -> bool:
    stamp = photo.created_at or photo.modified_at
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    age_days = (datetime.now(tz=when.tzinfo or timezone.utc) - when).days
    return age_days > 365 * 5


# Weak-but-usable candidates (no face, or low local score) are processed after the strong
# ones if the budget is tight. They are still kept for OpenAI.
def _is_low_priority(photo: Photo) -> bool:
    return (
        "local_flag_face_not_detected" in photo.soft_flags
        or "local_flag_low_local_score" in photo.soft_flags
    )


# Applies only minimal local rejection rules before OpenAI review, attaching soft flags
# instead of discarding photos on weak technical signals. Mutates the photo in place.
def apply_local_minimal_gate(photo: Photo, config: dict[str, Any]) -> Photo:
    gate_cfg = config.get("local_gate", {})
    mode = str(gate_cfg.get("mode", "minimal"))
    th = _thresholds(config)
    rules = gate_cfg.get("hard_reject", {}) or {}

    # Pre-existing terminal states from ingest/quality/duplicate detection.
    if photo.status == "rejected" and photo.reject_reason == "unreadable_image":
        return _decide(photo, "failed_read", eligible=False, hard_reason="failed_read")
    if photo.status == "rejected" and photo.reject_reason == "duplicate_exact":
        return _decide(photo, "hard_reject", eligible=False, hard_reason="exact_duplicate")
    if photo.status == "rejected":
        return _decide(photo, "hard_reject", eligible=False, hard_reason=photo.reject_reason or "rejected")

    # Strict/legacy mode: aggressive rejection parity, then stop.
    if mode == "strict":
        reason = _strict_reject_reason(photo, config)
        if reason:
            photo.status = "rejected"
            photo.reject_reason = reason
            return _decide(photo, "hard_reject", eligible=False, hard_reason=reason)

    # Minimal mode: extreme-only hard rejects.
    hard = _extreme_hard_reject_reason(photo, th, rules)
    if hard:
        photo.status = "rejected"
        photo.reject_reason = hard
        return _decide(photo, "hard_reject", eligible=False, hard_reason=hard)

    # Keep the photo: attach soft flags and classify it as an OpenAI candidate.
    _attach_soft_flags(photo, th, config)
    if photo.is_near_duplicate:
        return _decide(photo, "duplicate_candidate", eligible=True)
    if _is_low_priority(photo):
        return _decide(photo, "low_priority_openai_candidate", eligible=True)
    return _decide(photo, "openai_candidate", eligible=True)


# Runs the gate across a batch, emitting local_gate_progress with running counts. Returns
# a small summary dict (counts) for the caller/report.
def run_local_gate(photos: list[Photo], config: dict[str, Any]) -> dict[str, int]:
    total = len(photos)
    hard_rejected = 0
    eligible = 0
    for i, photo in enumerate(photos, start=1):
        apply_local_minimal_gate(photo, config)
        if photo.gate_status in {"hard_reject", "failed_read"}:
            hard_rejected += 1
        elif photo.eligible_for_openai:
            eligible += 1
        if i % 25 == 0 or i == total:
            events.local_gate_progress(i, total, hard_rejected, eligible)
    events.log(
        f"Local Minimal Gate: {eligible} eligible for OpenAI, {hard_rejected} hard-rejected.",
        "info",
    )
    return {"total": total, "hard_rejected": hard_rejected, "eligible_for_openai": eligible}
