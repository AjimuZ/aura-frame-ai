"""Export results: copy selected and rejected photos into ordered output folders.

Originals are only ever copied, never moved or modified. All output stays under the
configured (git-ignored) output directory.
"""

import shutil
from pathlib import Path
from typing import Any

from . import progress_events as events
from .utils import Photo, ensure_dir, resolve_path

# Final-set slot order mapped to friendly export file stems.
_FINAL_NAMES = {
    "main_photo": "01_main_photo",
    "full_body": "02_full_body",
    "activity_photo": "03_activity",
    "second_photo": "04_candid",
    "social_photo": "05_social",
    "elegant_photo": "06_elegant",
}

# Reject reason mapped to its output subfolder.
_REJECT_FOLDERS = {
    "too_blurry": "scartate_sfocate",
    "too_dark": "scartate_buie",
    "too_bright": "scartate_buie",
    "bad_aspect": "scartate_formato",
    "likely_screenshot": "scartate_screenshot",
    "duplicate": "scartate_duplicate",
    "duplicate_exact": "scartate_duplicate",
    "duplicate_similar": "scartate_duplicate",
    "crowded_group": "scartate_volto_non_chiaro",
    "no_face": "scartate_volto_non_chiaro",
    "unreadable_image": "scartate_volto_non_chiaro",
}


# Copies a file into a destination folder, preserving its extension; never modifies source.
def _copy(src: str, dest_dir: Path, stem: str | None = None) -> None:
    source = Path(src)
    if not source.exists():
        return
    ensure_dir(dest_dir)
    name = f"{stem}{source.suffix}" if stem else source.name
    shutil.copy2(source, dest_dir / name)


# Returns the output base directory from config.
def _output_base(config: dict[str, Any]) -> Path:
    return ensure_dir(resolve_path(config["output"]["base_dir"]))


# Exports the final six photos with ordered, slot-named filenames.
def export_final_set(final_set: list[Photo], config: dict[str, Any]) -> None:
    base = _output_base(config)
    dest = ensure_dir(base / "top_06_final_set")
    for photo in final_set:
        stem = _FINAL_NAMES.get(photo.best_slot)
        _copy(photo.file_path, dest, stem)
    events.log(f"Exported {len(final_set)} photos to top_06_final_set", "info")


# Exports the full set of review/curation folders from the analyzed photos.
def export_all(photos: list[Photo], config: dict[str, Any]) -> None:
    base = _output_base(config)
    accepted = sorted(
        (p for p in photos if p.status != "rejected"), key=lambda p: p.final_score, reverse=True
    )

    review_count = int(config["output"].get("top_review_count", 50))
    for photo in accepted[:20]:
        _copy(photo.file_path, base / "top_20_da_scegliere")
    for photo in accepted[:review_count]:
        _copy(photo.file_path, base / "top_50_review")

    for photo in photos:
        if photo.status == "rejected" and photo.reject_reason:
            folder = _REJECT_FOLDERS.get(photo.reject_reason)
            if folder:
                _copy(photo.file_path, base / folder)

    export_final_set([p for p in photos if p.status == "selected"], config)
    events.log("Export complete.", "info")


# Maps a selection slot to its per-slot OpenAI candidate folder (second_photo == candid).
_SLOT_CANDIDATE_FOLDERS = {
    "main_photo": "best_main_photo_candidates",
    "full_body": "best_full_body_candidates",
    "activity_photo": "best_activity_candidates",
    "second_photo": "best_candid_candidates",
    "social_photo": "best_social_candidates",
    "elegant_photo": "best_elegant_candidates",
}


# Formats a 0-10 score for a filename (9.4 -> "9_4").
def _score_stem(value: float) -> str:
    return f"{value:.1f}".replace(".", "_")


# Copies the ranked tiers into numbered files within a folder, ordered by consensus score.
def _export_ranked(photos: list[Photo], dest: Path, label: str) -> None:
    for i, photo in enumerate(photos, start=1):
        slot = photo.final_selected_slot or photo.best_slot
        stem = f"{i:03d}_score_{_score_stem(photo.consensus_score)}_{slot}"
        _copy(photo.file_path, dest, stem)
    if photos:
        events.log(f"Exported {len(photos)} photos to {label}", "info")


# Exports the OpenAI Quality Mode results into the openai_quality output tree (copy-only).
# Originals are never moved or modified; existing local output folders are left untouched.
def export_openai_quality(photos: list[Photo], final_set: list[Photo], config: dict[str, Any]) -> None:
    base = ensure_dir(_output_base(config) / "openai_quality")
    by_consensus = sorted(
        (p for p in photos if p.eligible_for_openai),
        key=lambda p: p.consensus_score,
        reverse=True,
    )

    # Top 6 final set, in fixed slot order with friendly names.
    top6_dir = ensure_dir(base / "top_06_final_set")
    for photo in final_set:
        stem = _FINAL_NAMES.get(photo.final_selected_slot or photo.best_slot)
        _copy(photo.file_path, top6_dir, stem)

    _export_ranked([p for p in by_consensus if p.in_top_20], base / "top_20_best", "top_20_best")
    _export_ranked([p for p in by_consensus if p.in_top_50], base / "top_50_review", "top_50_review")
    _export_ranked([p for p in by_consensus if p.in_top_100], base / "top_100_backup_good", "top_100_backup_good")

    # Per-slot candidate folders (top consensus photos whose slot matches).
    for slot, folder in _SLOT_CANDIDATE_FOLDERS.items():
        picks = [p for p in by_consensus if (p.final_selected_slot or p.best_slot) == slot][:20]
        _export_ranked(picks, base / folder, folder)

    # Needs-manual-review and OpenAI-rejected sets.
    review = [p for p in by_consensus if p.needs_manual_review]
    _export_ranked(review, base / "needs_manual_review", "needs_manual_review")
    rejected = [
        p
        for p in photos
        if (p.openai_broad or {}).get("reject")
        or (p.openai_premium or {}).get("reject_from_final_set")
        or p.failed_openai_review
    ]
    for photo in rejected:
        _copy(photo.file_path, base / "rejected_by_openai")
    events.log("OpenAI Quality Mode export complete.", "info")
