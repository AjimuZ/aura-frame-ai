"""Strict JSON schema validation for VLM photo reviews.

A response is accepted only if it parses as JSON and contains every required key. Optional
list/text fields are defaulted so downstream code can rely on their presence.
"""

import json
import re
from typing import Any

# The evaluation prompt. Identity is never requested; only dating-photo suitability.
PROMPT = """Analizza questa foto per un profilo dating maschile.

Contesto:
- La persona principale nella foto e sempre l'utente.
- Non devi riconoscere l'identita della persona.
- Devi valutare solo se la foto e adatta a una dating app.
- L'obiettivo e sembrare autentico, pulito, sicuro, interessante e approcciabile.
- Evita giudizi su etnia, religione, status sociale, salute o caratteristiche sensibili.

Valuta la foto secondo questi criteri:
1. qualita generale della foto;
2. chiarezza del volto;
3. luce;
4. espressione;
5. naturalezza;
6. outfit;
7. contesto;
8. se e adatta come prima foto;
9. se comunica sicurezza senza sembrare arrogante;
10. se comunica una personalita interessante.

Rispondi SOLO in JSON valido:

{
  "dating_score": 1,
  "technical_quality": 1,
  "face_clarity": 1,
  "expression_score": 1,
  "naturalness_score": 1,
  "outfit_score": 1,
  "context_score": 1,
  "category": "HEADSHOT | FULL_BODY | ACTIVITY | SOCIAL | CANDID | ELEGANT | BAD",
  "best_slot": "main_photo | second_photo | full_body | activity_photo | social_photo | elegant_photo | backup | reject",
  "strengths": [],
  "weaknesses": [],
  "reason": "spiegazione breve in italiano"
}
"""

# Numeric criteria, all on the 1-10 scale the prompt asks for and the ranker expects.
_SCORE_KEYS = (
    "dating_score",
    "technical_quality",
    "face_clarity",
    "expression_score",
    "naturalness_score",
    "outfit_score",
    "context_score",
)

REQUIRED_KEYS = {*_SCORE_KEYS, "category", "best_slot"}

# JSON Schema handed to Ollama as a structured-output constraint (Ollama >= 0.5). Forcing the
# exact keys and types is far more reliable than plain "json" mode, which thinking-capable or
# community fine-tunes routinely ignore (producing prose or a differently shaped object).
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{key: {"type": "integer"} for key in _SCORE_KEYS},
        "category": {
            "type": "string",
            "enum": ["HEADSHOT", "FULL_BODY", "ACTIVITY", "SOCIAL", "CANDID", "ELEGANT", "BAD"],
        },
        "best_slot": {
            "type": "string",
            "enum": [
                "main_photo", "second_photo", "full_body", "activity_photo",
                "social_photo", "elegant_photo", "backup", "reject",
            ],
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": sorted(REQUIRED_KEYS | {"strengths", "weaknesses", "reason"}),
}

# Thinking-capable models sometimes prepend chain-of-thought even when asked for JSON only.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


# Coerces a model score to an int clamped to 1-10, or None when it is not numeric.
def _coerce_score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and (match := _NUMBER.search(value)):
        number = float(match.group(0))
    else:
        return None
    return int(max(1.0, min(10.0, round(number))))


# Normalizes a strengths/weaknesses field into a clean list of non-empty strings.
def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# Descends into a single wrapper object if the model nested the review one level deep.
def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and not REQUIRED_KEYS.issubset(data.keys()):
        nested = [value for value in data.values() if isinstance(value, dict)]
        if len(nested) == 1 and REQUIRED_KEYS.issubset(nested[0].keys()):
            return nested[0]
    return data


# Parses and validates a model response string into a review dict, or None if unusable.
def parse_review(text: str) -> dict[str, Any] | None:
    cleaned = _THINK_BLOCK.sub("", text or "")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = _unwrap(json.loads(cleaned[start : end + 1]))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Every numeric criterion must be present and coercible; otherwise the review is junk.
    for key in _SCORE_KEYS:
        score = _coerce_score(data.get(key))
        if score is None:
            return None
        data[key] = score
    if not isinstance(data.get("category"), str) or not isinstance(data.get("best_slot"), str):
        return None
    data["strengths"] = _string_list(data.get("strengths"))
    data["weaknesses"] = _string_list(data.get("weaknesses"))
    data["reason"] = str(data.get("reason", "")).strip()
    return data
