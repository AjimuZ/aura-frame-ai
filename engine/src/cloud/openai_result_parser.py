"""Strict-but-tolerant JSON parsing for OpenAI Quality Mode responses.

Mirrors the robustness of the local VLM parser (src/vlm/schema.py): isolate the JSON
object, strip stray prose, coerce scores to a 1-10 float, and default optional fields. The
spec's `candid_photo` slot is mapped onto the codebase's canonical `second_photo`. A return
value of None means the response was unusable and the photo is flagged failed_openai_review.
"""

import json
import re
from typing import Any

# The spec uses `candid_photo`; the codebase's canonical fourth slot is `second_photo`
# (exported as 04_candid). Map on the way in so the rest of the engine stays unchanged.
_SLOT_ALIASES = {"candid_photo": "second_photo"}
_VALID_SLOTS = {
    "main_photo", "second_photo", "full_body", "activity_photo",
    "social_photo", "elegant_photo", "backup", "reject",
}
_VALID_CATEGORIES = {"HEADSHOT", "FULL_BODY", "ACTIVITY", "SOCIAL", "CANDID", "ELEGANT", "BAD"}

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


# Isolates and parses the first JSON object in a model response, or returns None.
def _extract_json(text: str) -> Any:
    cleaned = _THINK_BLOCK.sub("", text or "")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


# Coerces a value to a float clamped to 1-10, or None when it is not numeric.
def _score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and (match := _NUMBER.search(value)):
        number = float(match.group(0))
    else:
        return None
    return max(1.0, min(10.0, number))


# Coerces a score with a fallback default (used for non-critical secondary scores).
def _score_or(value: Any, default: float) -> float:
    parsed = _score(value)
    return parsed if parsed is not None else default


# Interprets common truthy/falsey encodings as a bool.
def _flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "si", "sì"}
    return default


# Normalizes a best_slot string: applies the candid alias and validates the enum.
def normalize_slot(value: Any) -> str:
    slot = str(value or "").strip().lower()
    slot = _SLOT_ALIASES.get(slot, slot)
    return slot if slot in _VALID_SLOTS else "backup"


# Normalizes a category string, defaulting to BAD when unrecognized.
def _category(value: Any) -> str:
    cat = str(value or "").strip().upper()
    return cat if cat in _VALID_CATEGORIES else "BAD"


# Cleans a short free-text field.
def _text(value: Any) -> str:
    return str(value or "").strip()


# Parses a Broad Scan response. Requires a numeric primary score; secondary fields default.
def parse_broad(text: str) -> dict[str, Any] | None:
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    score = _score(data.get("score"))
    if score is None:
        return None
    return {
        "score": score,
        "face": _score_or(data.get("face"), score),
        "naturalness": _score_or(data.get("naturalness"), score),
        "style": _score_or(data.get("style"), score),
        "context": _score_or(data.get("context"), score),
        "category": _category(data.get("category")),
        "best_slot": normalize_slot(data.get("best_slot")),
        "reject": _flag(data.get("reject")),
        "reason": _text(data.get("reason")),
    }


# Parses a Quality Review response.
def parse_quality(text: str) -> dict[str, Any] | None:
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    score = _score(data.get("score"))
    if score is None:
        return None
    return {
        "score": score,
        "main_photo_score": _score_or(data.get("main_photo_score"), score),
        "face_clarity": _score_or(data.get("face_clarity"), score),
        "naturalness": _score_or(data.get("naturalness"), score),
        "appeal": _score_or(data.get("appeal"), score),
        "confidence": _score_or(data.get("confidence"), score),
        "style_score": _score_or(data.get("style_score"), score),
        "context_score": _score_or(data.get("context_score"), score),
        "category": _category(data.get("category")),
        "best_slot": normalize_slot(data.get("best_slot")),
        "keep": _flag(data.get("keep"), True),
        "reason": _text(data.get("reason")),
        "risk": _text(data.get("risk")),
    }


# Parses a Premium Review response.
def parse_premium(text: str) -> dict[str, Any] | None:
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    score = _score(data.get("score"))
    if score is None:
        return None
    return {
        "score": score,
        "overall_value": _score_or(data.get("overall_value"), score),
        "selection_value": _score_or(data.get("selection_value"), score),
        "authenticity": _score_or(data.get("authenticity"), score),
        "confidence": _score_or(data.get("confidence"), score),
        "appeal": _score_or(data.get("appeal"), score),
        "style_score": _score_or(data.get("style_score"), score),
        "context_score": _score_or(data.get("context_score"), score),
        "best_slot": normalize_slot(data.get("best_slot")),
        "keep_for_top_20": _flag(data.get("keep_for_top_20"), True),
        "keep_for_top_50": _flag(data.get("keep_for_top_50"), True),
        "reject_from_final_set": _flag(data.get("reject_from_final_set")),
        "reason": _text(data.get("reason")),
        "risk": _text(data.get("risk")),
    }


# Parses the comparative Final Judge response into a normalized selection structure.
def parse_final_judge(text: str) -> dict[str, Any] | None:
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    raw_top6 = data.get("top_6") if isinstance(data.get("top_6"), list) else []
    top_6: list[dict[str, Any]] = []
    for item in raw_top6:
        if isinstance(item, dict) and item.get("photo_id"):
            top_6.append(
                {
                    "photo_id": str(item["photo_id"]),
                    "slot": normalize_slot(item.get("slot")),
                    "reason": _text(item.get("reason")),
                    "risk": _text(item.get("risk")),
                }
            )
    raw_slots = data.get("slot_candidates") if isinstance(data.get("slot_candidates"), dict) else {}
    slot_candidates: dict[str, list[str]] = {}
    for slot, ids in raw_slots.items():
        canonical = normalize_slot(slot)
        if isinstance(ids, list):
            slot_candidates[canonical] = [str(i) for i in ids if i]
    if not top_6 and not slot_candidates:
        return None
    return {
        "top_6": top_6,
        "top_20": [str(i) for i in data.get("top_20", []) if i] if isinstance(data.get("top_20"), list) else [],
        "slot_candidates": slot_candidates,
        "final_notes": _text(data.get("final_notes")),
    }


# Dispatches to the parser for the given stage.
def parse(stage: str, text: str) -> dict[str, Any] | None:
    return {
        "broad_scan": parse_broad,
        "quality_review": parse_quality,
        "premium_review": parse_premium,
        "final_judge": parse_final_judge,
    }[stage](text)
