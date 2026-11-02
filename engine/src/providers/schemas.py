"""Verdict JSON Schemas and a small validator for the subset of JSON Schema they use.

The validator is in-repo because the engine has no approved JSON Schema package. It supports
`type`, `enum`, `required`, `properties`, `additionalProperties: false`, `items`, `minItems`,
`maxItems`, `minLength` and `maxLength`, and rejects any other keyword in a schema so a
typo cannot silently turn a check off. Errors name a path and a rule, never the value, because
a value may contain model text.
"""

from typing import Any

from .contract import REASON_TAGS

COMPARISON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reasons": {
            "type": "array",
            "items": {"type": "string", "enum": list(REASON_TAGS)},
            "maxItems": len(REASON_TAGS),
        },
        # About 20 words; the schema can only count characters.
        "note": {"type": "string", "maxLength": 160},
    },
    "required": ["winner", "confidence", "reasons", "note"],
    "additionalProperties": False,
}

CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "strengths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 80},
            "maxItems": 5,
        },
        "weaknesses": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 80},
            "maxItems": 5,
        },
        "caption": {"type": "string", "maxLength": 200},
    },
    "required": ["strengths", "weaknesses", "caption"],
    "additionalProperties": False,
}

_KNOWN_KEYWORDS = {
    "type", "enum", "required", "properties", "additionalProperties", "items",
    "minItems", "maxItems", "minLength", "maxLength",
}

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


# Tells whether a value has a JSON type; a bool is never an integer or a number.
def _has_type(value: Any, name: str) -> bool:
    if name not in _TYPES:
        raise ValueError(f"Unsupported schema type: {name}")
    if isinstance(value, bool) and name != "boolean":
        return False
    return isinstance(value, _TYPES[name])


# Returns the rule violations of an instance against a schema, as "path: rule" strings.
def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    unknown = set(schema) - _KNOWN_KEYWORDS
    if unknown:
        raise ValueError(f"Unsupported schema keyword: {sorted(unknown)[0]}")
    if "type" in schema and not _has_type(instance, schema["type"]):
        return [f"{path}: expected {schema['type']}"]
    errors: list[str] = []
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: not one of the allowed values")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than minLength")
        if len(instance) > schema.get("maxLength", len(instance)):
            errors.append(f"{path}: longer than maxLength")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than minItems")
        if len(instance) > schema.get("maxItems", len(instance)):
            errors.append(f"{path}: more than maxItems")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}.{key}: missing")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}.{key}: not allowed")
        for key, subschema in properties.items():
            if key in instance:
                errors.extend(validate(instance[key], subschema, f"{path}.{key}"))
    return errors
