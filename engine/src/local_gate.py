"""Sanity gate: rejects only objective failures and flags everything else.

The gate removes a photo only when it is objectively unusable: an unreadable file (recorded as
failed_read), an unsupported image format, a byte-identical copy of an earlier photo, or an image
whose long side is under the configured minimum (256 px by default). It does NOT judge quality:
brightness and blur never reject (a night sky, fog or a smooth gradient is not a defect), and
screenshots and documents only get a soft flag. Rule of thumb: "when in doubt, keep it."

It runs after quality + face measurement and assigns each photo a gate_status, soft flags,
eligibility, and a processing priority. Only hard_reject / failed_read are excluded from later
stages; everything else flows forward.
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

MIN_LONG_SIDE = 256

# Soft-flag thresholds only: none of these can reject a photo.
_DEFAULT_THRESHOLDS: dict[str, float] = {
    "possible_blur_threshold": 120,
    "possible_dark_brightness": 35,
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


# Returns the minimum long side in pixels below which an image is rejected: the configured
# local_gate.min_long_side, or MIN_LONG_SIDE when absent or not a number.
def _min_long_side(config: dict[str, Any]) -> int:
    try:
        return int(config.get("local_gate", {}).get("min_long_side", MIN_LONG_SIDE))
    except (TypeError, ValueError):
        return MIN_LONG_SIDE


# Returns "too_small" when the decoded image has a long side under the minimum, else "". Sizes
# of 0 mean "not measured" and never reject. Only the size can reject here, never the content.
def _size_reject_reason(photo: Photo, config: dict[str, Any]) -> str:
    if photo.width and photo.height and max(photo.width, photo.height) < _min_long_side(config):
        return "too_small"
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
    # A screenshot is flagged, never rejected; scoring ranks it low. Documents will be flagged the
    # same way from their content tag (P04.4); no gate rule may reject either.
    if photo.likely_screenshot:
        _flag(photo, "local_flag_screenshot")


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


# Decides one photo: rejects it only for an objective failure and otherwise keeps it with soft
# flags. Rejections: unreadable (gate_status "failed_read"); exact duplicate, unsupported format
# or too small (gate_status "hard_reject" with hard_reject_reason "exact_duplicate",
# "unsupported_format" or "too_small"). Brightness, blur and screenshots never reject.
# Mutates the photo in place.
def apply_local_minimal_gate(photo: Photo, config: dict[str, Any]) -> Photo:
    th = _thresholds(config)

    # Pre-existing terminal states from ingest/quality/duplicate detection.
    if photo.status == "rejected" and photo.reject_reason == "unreadable_image":
        return _decide(photo, "failed_read", eligible=False, hard_reason="failed_read")
    if photo.status == "rejected" and photo.reject_reason == "duplicate_exact":
        return _decide(photo, "hard_reject", eligible=False, hard_reason="exact_duplicate")
    if photo.status == "rejected":
        return _decide(photo, "hard_reject", eligible=False, hard_reason=photo.reject_reason or "rejected")

    too_small = _size_reject_reason(photo, config)
    if too_small:
        photo.status = "rejected"
        photo.reject_reason = too_small
        return _decide(photo, "hard_reject", eligible=False, hard_reason=too_small)

    # Keep the photo: attach soft flags and classify it as a candidate.
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
