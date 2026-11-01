"""Append-only JSON Lines cache for OpenAI Quality Mode results.

A photo already analyzed with the same image hash + stage + model + prompt_version is not
re-sent to the API, saving both money and time across re-runs. The cache lives under the
git-ignored working directory and contains only scores/verdicts, never raw images.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import working_dir


# Returns the cache file path under the working directory.
def cache_path(config: dict[str, Any]) -> Path:
    return working_dir(config) / "openai_cache.jsonl"


# Builds the cache key from the fields that fully determine a result.
def cache_key(photo_hash: str, stage: str, model: str, prompt_version: str) -> str:
    return f"{photo_hash}|{stage}|{model}|{prompt_version}"


# Loads the cache into a dict keyed by cache_key. Tolerates partial/corrupt lines.
def load_cache(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    path = cache_path(config)
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = cache_key(
            record.get("photo_hash", ""),
            record.get("stage", ""),
            record.get("model", ""),
            record.get("prompt_version", ""),
        )
        out[key] = record
    return out


# Appends one cache record (single JSONL line). Creates the file/dirs as needed.
def append_cache(
    config: dict[str, Any],
    *,
    photo_hash: str,
    stage: str,
    model: str,
    prompt_version: str,
    result_json: dict[str, Any],
    estimated_cost_usd: float,
) -> None:
    record = {
        "photo_hash": photo_hash,
        "stage": stage,
        "model": model,
        "prompt_version": prompt_version,
        "result_json": result_json,
        "estimated_cost_usd": round(float(estimated_cost_usd), 6),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    path = cache_path(config)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
