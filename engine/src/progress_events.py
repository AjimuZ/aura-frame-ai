"""JSONL progress event emitters for the AuraFrame AI engine.

Every event is a single JSON object printed on its own line to stdout so the Electron
main process can parse the stream line-by-line.
"""

import json
import sys
from typing import Any


# Emits a single JSON event as one line on stdout and flushes immediately.
def emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# Emits a pipeline progress event for the given step.
def progress(step: str, current: int, total: int, message: str) -> None:
    emit({"type": "progress", "step": step, "current": current, "total": total, "message": message})


# Emits a per-photo scored event used to update the UI incrementally.
def photo_scored(photo_id: str, final_score: float, category: str) -> None:
    emit(
        {
            "type": "photo_scored",
            "photoId": photo_id,
            "finalScore": round(float(final_score), 2),
            "category": category,
        }
    )


# Emits Local Minimal Gate progress with running hard-reject / eligible counts.
def local_gate_progress(current: int, total: int, hard_rejected: int, openai_candidates: int) -> None:
    emit(
        {
            "type": "local_gate_progress",
            "current": current,
            "total": total,
            "hard_rejected": hard_rejected,
            "openai_candidates": openai_candidates,
            "message": "Applying Local Minimal Gate",
        }
    )


# Emits OpenAI stage progress including the running cost estimate and remaining budget.
def openai_progress(
    stage: str,
    current: int,
    total: int,
    estimated_cost_usd: float,
    budget_remaining_usd: float,
    message: str = "OpenAI review in progress",
) -> None:
    emit(
        {
            "type": "openai_progress",
            "stage": stage,
            "current": current,
            "total": total,
            "estimated_cost_usd": round(float(estimated_cost_usd), 4),
            "budget_remaining_usd": round(float(budget_remaining_usd), 4),
            "message": message,
        }
    )


# Emits a per-photo OpenAI score event for incremental UI updates.
def openai_photo_scored(photo_id: str, stage: str, score: float, best_slot: str) -> None:
    emit(
        {
            "type": "openai_photo_scored",
            "photo_id": photo_id,
            "stage": stage,
            "score": round(float(score), 2),
            "best_slot": best_slot,
        }
    )


# Emits a soft budget-limit warning (analysis continues).
def budget_warning(spent_estimate_usd: float, message: str = "Soft budget limit reached") -> None:
    emit(
        {
            "type": "budget_warning",
            "spent_estimate_usd": round(float(spent_estimate_usd), 4),
            "message": message,
        }
    )


# Emits a hard budget-limit stop event (no further OpenAI requests will be made).
def budget_stopped(
    spent_estimate_usd: float, message: str = "Hard budget limit reached, stopping OpenAI requests"
) -> None:
    emit(
        {
            "type": "budget_stopped",
            "spent_estimate_usd": round(float(spent_estimate_usd), 4),
            "message": message,
        }
    )


# Emits a free-form log line at the given level.
def log(message: str, level: str = "info") -> None:
    emit({"type": "log", "level": level, "message": message})


# Emits a terminal success event.
def done(message: str) -> None:
    emit({"type": "done", "message": message})


# Emits a terminal error event.
def error(message: str) -> None:
    emit({"type": "error", "message": message})
