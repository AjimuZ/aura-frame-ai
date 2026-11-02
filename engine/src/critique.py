"""Critique stage: one structured explanation for each finalist, never a factor in the rank.

The stage receives only photo IDs and the way to load their anonymous copies. It has no access to ranks, strengths or
prescores and returns nothing but text, so it cannot reorder anything. It runs after the selection is fixed. The text
comes from a model: it is stored as received (trimmed and bounded) and escaped where it is rendered.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Sequence

from . import progress_events as events
from .providers import CRITIQUE_PROMPT, Critique, Prompt, VisionProvider

# Bounds on what a critique may hold, whatever the model returns.
MAX_ITEMS = 5
MAX_ITEM_CHARS = 120
MAX_CAPTION_CHARS = 200
# A provider that fails this many finalists in a row is not asked again in this run.
MAX_FAILED_IN_A_ROW = 3

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class CritiqueNote:
    """The bounded critique of one finalist. Text is raw model output and must be escaped when rendered."""

    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    caption: str
    provider: str
    model: str
    prompt_version: str


@dataclass
class CritiqueResult:
    """What the stage produced: `notes` by photo ID, and how many finalists got none.

    `skipped` is True when no critique was attempted, with the `reason` ("no_provider", "provider_not_ready" or
    "no_finalists").
    """

    notes: dict[str, CritiqueNote]
    failed: int = 0
    provider_calls: int = 0
    skipped: bool = False
    reason: str | None = None


# Collapses control characters and runs of spaces, then cuts the text to `limit` characters.
def _clean(value: Any, limit: int) -> str:
    text = _CONTROL.sub(" ", value) if isinstance(value, str) else ""
    return " ".join(text.split())[:limit]


# Bounds a list of phrases: non-text and empty entries are dropped, at most MAX_ITEMS remain.
def _clean_items(values: Sequence[Any]) -> tuple[str, ...]:
    cleaned = (_clean(value, MAX_ITEM_CHARS) for value in values)
    return tuple([item for item in cleaned if item][:MAX_ITEMS])


# Turns a provider answer into a bounded note, or None when the answer is a failure or holds no text at all.
def _note_from(result: Critique) -> CritiqueNote | None:
    if not result.ok:
        return None
    note = CritiqueNote(
        _clean_items(result.strengths),
        _clean_items(result.weaknesses),
        _clean(result.caption, MAX_CAPTION_CHARS),
        result.provider,
        result.model,
        result.prompt_version,
    )
    return note if note.strengths or note.weaknesses or note.caption else None


# Critiques each finalist once, in the order given. `copy_for(photo_id)` returns the prepared anonymous copy. With no
# provider or one that is not ready the stage is skipped. A failed call or unreadable copy leaves that finalist
# without a note; after MAX_FAILED_IN_A_ROW in a row the rest are not attempted. Never raises for a provider failure,
# and never touches a rank: it takes IDs and returns text only.
def critique_finalists(
    finalist_ids: Sequence[str],
    provider: VisionProvider | None,
    copy_for: Callable[[str], bytes],
    prompt: Prompt = CRITIQUE_PROMPT,
) -> CritiqueResult:
    unique = list(dict.fromkeys(finalist_ids))
    if not unique:
        return CritiqueResult({}, skipped=True, reason="no_finalists")
    if provider is None:
        return CritiqueResult({}, skipped=True, reason="no_provider")
    try:
        ready = provider.health().ready
    except Exception:  # noqa: BLE001 - a probe that raises counts as not ready
        ready = False
    if not ready:
        events.log("The judge is not ready; the finalists get no critique.", "warning")
        return CritiqueResult({}, skipped=True, reason="provider_not_ready")

    notes: dict[str, CritiqueNote] = {}
    failed = calls = in_a_row = 0
    for position, photo_id in enumerate(unique, start=1):
        if in_a_row >= MAX_FAILED_IN_A_ROW:
            failed += len(unique) - position + 1
            break
        note = None
        try:
            image = copy_for(photo_id)
        except (OSError, ValueError):
            image = None
        if image is not None:
            calls += 1
            try:
                note = _note_from(provider.critique(image, prompt))
            except Exception:  # noqa: BLE001 - the contract says critique never raises; one bad call must not stop a run
                note = None
        if note is None:
            failed += 1
            in_a_row += 1
        else:
            notes[photo_id] = note
            in_a_row = 0
        events.progress("critique", position, len(unique), f"critique {position}/{len(unique)}")
    return CritiqueResult(notes, failed, calls)
