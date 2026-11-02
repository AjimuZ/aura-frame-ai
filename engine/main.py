"""AuraFrame AI engine CLI entry point.

Subcommands:
  index                Discover and index photos in a folder.
  analyze              Run the full local pipeline (index -> quality -> faces -> gate ->
                       qrealign -> siglip2 embeddings -> aesthetic -> prerank -> vlm -> rank).
                       Resumable.
  gemma-review         Run local AI review over the pre-ranked candidates.
  export-final         Export the selected final set to ordered folders.
  clear-data           Delete all generated data (DB, thumbnails, working, reports, output).
  local-minimal-gate   Run the Local Minimal Gate only (no cloud); writes the gate report.
  openai-estimate      Estimate OpenAI Quality Mode cost (no API calls).
  openai-quality       Run the OPTIONAL OpenAI Quality Mode (cloud; off unless enabled).
  export-openai-results  Re-export OpenAI Quality Mode outputs + reports from stored results.

The local pipeline is fully local; originals are read-only and never modified. OpenAI
Quality Mode is optional, off by default, and only ever sends resized, EXIF-stripped copies.
"""

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

# Make the sibling `src` package importable when run as `python engine/main.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import db  # noqa: E402
from src import local_gate  # noqa: E402
from src import progress_events as events  # noqa: E402
from src.aesthetic import score_aesthetic  # noqa: E402
from src.cloud import (  # noqa: E402
    BudgetGuard,
    MissingApiKeyError,
    ModelNotAvailableError,
    OpenAiClient,
    cost_estimator,
    is_cloud_enabled,
    rank_for_openai,
    run_final_judge,
    run_stage,
)
from src.embeddings import assign_clusters, grouping_key  # noqa: E402
from src.export_results import export_all, export_final_set, export_openai_quality  # noqa: E402
from src.face_quality import score_faces  # noqa: E402
from src.final_ranker import (  # noqa: E402
    apply_diversity_rerank,
    prerank_candidates,
    rank_photo,
    select_final_set,
    tag_conflicts,
)
from src.generate_report import (  # noqa: E402
    generate_openai_reports,
    generate_reports,
    write_gate_report,
)
from src.index_photos import index_photos  # noqa: E402
from src.index_photos import mark_duplicates  # noqa: E402
from src.content_tags import tag_content  # noqa: E402
from src.neural_stages import embed_siglip2, score_qrealign  # noqa: E402
from src.quality_score import score_quality  # noqa: E402
from src.vlm import judge_candidates  # noqa: E402
from src.utils import (  # noqa: E402
    Photo,
    apply_data_root,
    apply_preset,
    ensure_dir,
    load_config,
    load_dotenv,
    resolve_path,
    should_stop,
    thumbs_dir,
    working_dir,
)


# Loads config and applies optional --data-root, --exports-root and --preset overrides in one place.
def _prepare_config(args: argparse.Namespace) -> dict:  # type: ignore[type-arg]
    config = load_config(args.config)
    apply_data_root(config, getattr(args, "data_root", None), getattr(args, "exports_root", None))
    apply_preset(config, getattr(args, "preset", None))
    return config


# Runs the full local analysis pipeline. Returns 0 when done, 1 when the folder is missing and 2 when cancelled.
#
# Resume contract: every per-photo stage stores its results first and then marks the photo done for that stage in
# the `jobs` table, per photo or per GPU batch. A run that is killed, crashes or is cancelled therefore loses at most
# the photo or batch in flight, and the next run skips every photo a stage finished: nothing finished is decoded,
# measured, scored, embedded or tagged again. A photo ingested anew (its derived files were missing) has its stage
# states cleared, so each stage runs on it again. Grouping is reused while its inputs are unchanged. The steps over
# the whole collection that only read stored values (duplicate marks, gate report, ranking, reports and the mirror)
# are recomputed on every run. Pause and cancel are honored between photos and between batches.
def cmd_analyze(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    folder = resolve_path(args.input)
    if not folder.is_dir():
        events.error(f"Input folder does not exist: {folder}")
        return 1

    conn = db.connect(config)
    include_subfolders = not args.no_subfolders

    # The photo ID is the content hash, so an ingested photo with the same bytes is reused as stored (not decoded
    # again), also when the file was moved or renamed. "rank" covers libraries analyzed before ingest was recorded.
    ingested = db.done_ids(conn, "ingest") | db.done_ids(conn, "rank")
    known = {p.id: p for p in db.load_photos(conn) if p.id in ingested}
    photos = index_photos(folder, config, include_subfolders, known=known, checkpoint=_ingest_checkpoint(conn, config))
    if should_stop(config):
        return _cancel(conn, config)
    resumed = sum(1 for photo in photos if photo.carried_over)
    if resumed:
        events.log(f"Resuming: {resumed} unchanged photos are not decoded again.", "info")

    duplicates = mark_duplicates(photos, int(config["filters"].get("phash_distance", 5)))
    events.progress("duplicates", len(photos), len(photos), f"Found {duplicates} duplicates")
    for photo in photos:
        db.upsert_photo(conn, photo)
    if should_stop(config):
        return _cancel(conn, config)

    if _stage(conn, _pending(conn, photos, "quality"), config, "quality", score_quality):
        return _cancel(conn, config)

    if _face_stage(conn, _pending(conn, photos, "faces"), config):
        return _cancel(conn, config)

    # Sanity gate: reject only objective failures and soft-flag the rest. Rejected photos are gated too, so their
    # gate status is recorded. A photo gated by an earlier run keeps its stored decision.
    gated = _pending(conn, photos, "local_gate", include_rejected=True)
    local_gate.run_local_gate(gated, config)
    for photo in gated:
        db.upsert_photo(conn, photo)
    db.set_stages(conn, [photo.id for photo in gated], "local_gate", "done")
    write_gate_report(photos, config)
    if should_stop(config):
        return _cancel(conn, config)

    # Q-ReAlign quality and aesthetic values (0 to 1, GPU batches) follow the preset's aesthetic switch. They are
    # stored only; nothing ranks with them until the prescore arrives. A missing model leaves them unknown and the
    # photos pending, so a later run with the model scores them.
    if config.get("aesthetic", {}).get("enabled", True):
        pending = _pending(conn, photos, "qrealign")
        if pending:
            score_qrealign(pending, config, conn, checkpoint=_batch_checkpoint(conn, config, "qrealign"))
    if should_stop(config):
        return _cancel(conn, config)

    if config.get("embeddings", {}).get("enabled", True):
        pending = _pending(conn, photos, "embeddings")
        if pending:
            embed_siglip2(pending, config, conn, checkpoint=_batch_checkpoint(conn, config, "embeddings"))
        if should_stop(config):
            return _cancel(conn, config)
        # Only photos whose embedding stage finished are tagged, so a photo is never marked tagged before its vector
        # could exist.
        embedded = db.done_ids(conn, "embeddings")
        pending = [photo for photo in _pending(conn, photos, "content_tags") if photo.id in embedded]
        if pending:
            tag_content(pending, config, conn, checkpoint=_batch_checkpoint(conn, config, "content_tags"))
        if should_stop(config):
            return _cancel(conn, config)
        _group(conn, photos, config)
    if should_stop(config):
        return _cancel(conn, config)

    if config.get("aesthetic", {}).get("enabled", True):
        pending = _pending(conn, photos, "aesthetic")
        if pending:
            score_aesthetic(pending, config, conn)
            for photo in pending:
                db.upsert_photo(conn, photo)
            db.set_stages(conn, [photo.id for photo in pending], "aesthetic", "done")
    if should_stop(config):
        return _cancel(conn, config)

    # Preliminary ranking (technical + face + aesthetic) to choose AI candidates.
    for photo in photos:
        rank_photo(photo, config)

    candidates = prerank_candidates(photos, int(config["output"]["vlm_candidate_count"]))
    events.progress("preranking", len(candidates), len(candidates), "Selected candidates")

    run_vlm = args.gemma and config.get("_vlm_enabled", True)
    if run_vlm:
        judge_candidates(candidates, config)
        for photo in candidates:
            rank_photo(photo, config)

    apply_diversity_rerank(photos, config)
    final_set = select_final_set(photos)
    events.progress("final_set", len(final_set), len(final_set), "Built final set")

    for photo in photos:
        db.upsert_photo(conn, photo)
    db.set_stages(conn, [photo.id for photo in photos], "rank", "done")
    db.write_mirror(conn, config)
    generate_reports(photos, config)
    export_all(photos, config)
    conn.close()
    events.done(f"Analyzed {len(photos)} photos; selected {len(final_set)} for the final set.")
    return 0


# Indexes a folder without scoring and persists the index.
def cmd_index(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    folder = resolve_path(args.input)
    if not folder.is_dir():
        events.error(f"Input folder does not exist: {folder}")
        return 1
    conn = db.connect(config)
    known = {p.id: p for p in db.load_photos(conn)}
    photos = index_photos(folder, config, not args.no_subfolders, known=known)
    for photo in photos:
        if not photo.carried_over:
            db.clear_stages(conn, photo.id)  # decoded anew: no earlier stage result describes this record
        db.upsert_photo(conn, photo)
        db.set_stage(conn, photo.id, "ingest", "done")
    db.write_mirror(conn, config)
    conn.close()
    events.done(f"Indexed {len(photos)} photos.")
    return 0


# Runs AI review over previously analyzed photos and re-ranks them.
def cmd_gemma_review(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    conn = db.connect(config)
    photos = db.load_photos(conn)
    if not photos:
        events.error("No analyzed photos found. Run 'analyze' first.")
        return 1
    limit = args.limit or int(config["output"]["vlm_candidate_count"])
    candidates = prerank_candidates(photos, limit)
    judge_candidates(candidates, config)
    for photo in candidates:
        rank_photo(photo, config)
    apply_diversity_rerank(photos, config)
    for photo in photos:
        db.upsert_photo(conn, photo)
    db.write_mirror(conn, config)
    conn.close()
    events.done(f"AI review complete for {len(candidates)} candidates.")
    return 0


# Exports the final selected set (optionally restricted to specific ids).
def cmd_export_final(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    conn = db.connect(config)
    photos = db.load_photos(conn)
    ids = {i for i in (args.ids or "").split(",") if i}
    if ids:
        selected = [p for p in photos if p.id in ids]
    else:
        selected = [p for p in photos if p.status == "selected"]
    export_final_set(selected, config)
    conn.close()
    events.done(f"Exported {len(selected)} photos to the final set.")
    return 0


# Persists a manual review session (slot assignments, per-slot locks, shortlist) to SQLite.
def cmd_save_review(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    try:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        events.error(f"Could not read review session: {exc}")
        return 1
    conn = db.connect(config)
    session_id = db.save_review_session(
        conn,
        payload.get("slotAssignments", {}),
        payload.get("locks", {}),
        payload.get("shortlist", []),
    )
    conn.close()
    events.done(f"Saved review session {session_id}.")
    return 0


# Returns the latest manual review session from the selected library database.
def cmd_load_review(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    conn = db.connect(config)
    session = db.load_latest_review_session(conn)
    conn.close()
    events.emit({"type": "review_session", "session": session})
    events.done("Review session loaded.")
    return 0


# Deletes every generated artifact (DB, thumbs, working, reports, output). Never originals.
def cmd_clear_data(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    targets = [
        working_dir(config),
        thumbs_dir(config),
        ensure_dir(resolve_path(config["output"]["base_dir"])),
        ensure_dir(resolve_path(config["output"]["reports_dir"])),
    ]
    removed = 0
    for target in targets:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
    events.done(f"Cleared {removed} generated-data locations.")
    return 0


# Runs the Local Minimal Gate over a folder (no cloud): measure quality + faces, mark
# duplicates, then classify each photo (reject only objective failures; soft-flag the rest).
# Produces the gate report and the photos.json the OpenAI flow consumes.
def cmd_local_minimal_gate(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    folder = resolve_path(args.input)
    if not folder.is_dir():
        events.error(f"Input folder does not exist: {folder}")
        return 1
    conn = db.connect(config)
    photos = index_photos(folder, config, not args.no_subfolders)
    mark_duplicates(photos, int(config["filters"].get("phash_distance", 5)))
    total = len(photos)
    for i, photo in enumerate(photos, start=1):
        if photo.status != "rejected":
            score_quality(photo, config)
        if photo.status != "rejected":
            score_faces(photo, config)
        if i % 25 == 0 or i == total:
            events.progress("quality", i, total, f"Measuring {i}/{total}")
    local_gate.run_local_gate(photos, config)
    for photo in photos:
        rank_photo(photo, config)  # local technical score kept only as consensus metadata
        db.upsert_photo(conn, photo)
        db.set_stage(conn, photo.id, "local_gate", "done")
    db.write_mirror(conn, config)
    report = write_gate_report(photos, config)
    conn.close()
    eligible = sum(1 for p in photos if p.eligible_for_openai)
    events.done(
        f"Local Minimal Gate complete: {eligible}/{len(photos)} eligible for OpenAI. "
        f"Report: {report.name}"
    )
    return 0


# Estimates the OpenAI Quality Mode cost from the gated photo set WITHOUT calling the API.
def cmd_openai_estimate(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    conn = db.connect(config)
    photos = db.load_photos(conn)
    conn.close()
    eligible = [p for p in photos if p.eligible_for_openai]
    if not eligible:
        events.error("No OpenAI-eligible photos. Run 'local-minimal-gate' (or 'analyze') first.")
        return 1
    limits = config.get("openai", {}).get("limits", {})
    broad_n = min(len(eligible), int(limits.get("broad_scan_max_photos", 4000)))
    quality_n = min(broad_n, int(limits.get("quality_review_max_photos", 1000)))
    premium_n = min(quality_n, int(limits.get("premium_review_max_photos", 200)))
    final_n = min(premium_n, int(limits.get("final_judge_max_photos", 40)))
    per_stage = {
        "broad_scan": cost_estimator.estimate_stage_cost("broad_scan", broad_n, config),
        "quality_review": cost_estimator.estimate_stage_cost("quality_review", quality_n, config),
        "premium_review": cost_estimator.estimate_stage_cost("premium_review", premium_n, config),
        "final_judge": cost_estimator.estimate_stage_cost("final_judge", final_n, config),
    }
    total = round(sum(per_stage.values()), 2)
    budget = config.get("openai", {}).get("budget", {})
    hard_stop = float(budget.get("hard_stop_usd", budget.get("max_usd", 16.0)))
    events.emit(
        {
            "type": "openai_estimate",
            "eligible": len(eligible),
            "counts": {
                "broad_scan": broad_n,
                "quality_review": quality_n,
                "premium_review": premium_n,
                "final_judge": final_n,
            },
            "per_stage_usd": {k: round(v, 2) for k, v in per_stage.items()},
            "estimated_total_usd": total,
            "budget_max_usd": float(budget.get("max_usd", 16.0)),
            "within_budget": total <= hard_stop,
        }
    )
    events.done(f"Estimated OpenAI cost: ${total:.2f} for {len(eligible)} eligible photos.")
    return 0


# Runs OpenAI Quality Mode (optional cloud review). Refuses to run unless the user explicitly
# enabled both privacy.allow_cloud_api and openai.enabled. Only resized, EXIF-stripped copies
# are sent; the budget guard caps spend and the pipeline is resumable across stages.
def cmd_openai_quality(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    # --enable-cloud represents an explicit user action (e.g. the desktop toggle) turning on
    # cloud review for this run. config.yaml stays off-by-default; this never persists.
    if getattr(args, "enable_cloud", False):
        config.setdefault("privacy", {})["allow_cloud_api"] = True
        config.setdefault("openai", {})["enabled"] = True
    if not is_cloud_enabled(config):
        events.error(
            "OpenAI Quality Mode is disabled. Set privacy.allow_cloud_api: true and "
            "openai.enabled: true to use it."
        )
        return 1
    conn = db.connect(config)
    photos = db.load_photos(conn)
    if not photos:
        conn.close()
        events.error("No photos found. Run 'local-minimal-gate' (or 'analyze') first.")
        return 1
    if not any(p.gate_status for p in photos):
        conn.close()
        events.error("Photos have not passed the Local Minimal Gate. Run 'local-minimal-gate' first.")
        return 1
    client = OpenAiClient(config)
    if not client.has_key:
        conn.close()
        events.error("OPENAI_API_KEY is not set. Configure it before running OpenAI Quality Mode.")
        return 1

    for photo in photos:
        if photo.eligible_for_openai and not photo.final_score:
            rank_photo(photo, config)
    budget = BudgetGuard(config, override_max=(float(args.budget) if args.budget else None))

    # Persists the in-memory photo set (incremental durability + UI mirror).
    def persist() -> None:
        for photo in photos:
            db.upsert_photo(conn, photo)
        db.write_mirror(conn, config)

    single = ["broad_scan", "quality_review", "premium_review"]
    stages = [args.stage] if args.stage else single + ["final_judge"]
    judge_result: dict | None = None
    try:
        for stage in stages:
            if should_stop(config):
                persist()
                conn.close()
                events.error("OpenAI Quality Mode cancelled.")
                return 2
            if budget.stopped:
                events.log("Budget hard stop reached; skipping remaining stages.", "warning")
                break
            if stage == "final_judge":
                judge_result = run_final_judge(photos, config, budget=budget, client=client)
            else:
                run_stage(stage, photos, config, budget=budget, client=client, persist=persist)
            for photo in photos:
                db.set_stage(conn, photo.id, stage, "done")
            persist()
    except MissingApiKeyError as exc:
        persist()
        conn.close()
        events.error(str(exc))
        return 1
    except ModelNotAvailableError as exc:
        persist()
        conn.close()
        events.error(f"OpenAI model unavailable: {exc}")
        return 1

    tag_conflicts(photos)
    final_set = rank_for_openai(photos, config, judge_result)
    persist()
    generate_openai_reports(photos, config)
    export_openai_quality(photos, final_set, config)
    conn.close()
    events.done(
        f"OpenAI Quality Mode complete. Estimated spend ${budget.spent_usd:.2f}. "
        f"Top 6 selected: {len(final_set)}."
    )
    return 0


# Probes the OpenAI API: key presence, reachability, and per-stage resolved model. Read-only.
def cmd_openai_check(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    report = OpenAiClient(config).check()
    events.emit({"type": "openai_check", **report})
    events.done(report.get("message", "ok"))
    return 0


# Re-exports the OpenAI Quality Mode output folders + reports from the stored results.
def cmd_export_openai_results(args: argparse.Namespace) -> int:
    config = _prepare_config(args)
    conn = db.connect(config)
    photos = db.load_photos(conn)
    conn.close()
    if not photos:
        events.error("No photos found. Run OpenAI Quality Mode first.")
        return 1
    final_set = rank_for_openai(photos, config, None)
    generate_openai_reports(photos, config)
    export_openai_quality(photos, final_set, config)
    events.done("Exported OpenAI Quality Mode results.")
    return 0


# Returns the photos whose `stage` is not recorded as done, in their current order. Rejected photos are left out
# unless `include_rejected`, since the scoring stages skip them and must not load a model for them.
def _pending(conn, photos: list[Photo], stage: str, include_rejected: bool = False) -> list[Photo]:  # type: ignore[no-untyped-def]
    done = db.done_ids(conn, stage)
    return [p for p in photos if p.id not in done and (include_rejected or p.status != "rejected")]


# Returns the ingest checkpoint: it stores each new record at once and marks it ingested, then says whether the run
# must stop. A record that was not reused from storage describes freshly decoded content, so its earlier stage
# states are cleared first and every stage runs on it again.
def _ingest_checkpoint(conn, config: dict) -> Callable[[Photo], bool]:  # type: ignore[no-untyped-def]
    def record(photo: Photo) -> bool:
        if not photo.carried_over:
            db.clear_stages(conn, photo.id)
        db.upsert_photo(conn, photo)  # also records the new path of a moved file
        db.set_stage(conn, photo.id, "ingest", "done")
        return should_stop(config)

    return record


# Returns the checkpoint of a batch stage: it marks the batch done for `stage` (its results are already stored),
# then says whether the run must stop, blocking while the run is paused.
def _batch_checkpoint(conn, config: dict, stage: str) -> Callable[[Sequence[Photo]], bool]:  # type: ignore[no-untyped-def]
    def record(batch: Sequence[Photo]) -> bool:
        db.set_stages(conn, [photo.id for photo in batch], stage, "done")
        return should_stop(config)

    return record


# Applies a per-photo scoring function to `photos`, storing each photo and then marking it done for `step`.
# Returns True when the run must stop (cancel); a pause blocks between photos until it is lifted.
def _stage(conn, photos: list[Photo], config: dict, step: str, func) -> bool:  # type: ignore[no-untyped-def]
    total = len(photos)
    for i, photo in enumerate(photos, start=1):
        if photo.status != "rejected":
            func(photo, config)
        db.upsert_photo(conn, photo)
        db.set_stage(conn, photo.id, step, "done")
        if i % 25 == 0 or i == total:
            events.progress(step, i, total, f"{step} {i}/{total}")
        if should_stop(config):
            return True
    return False


# Runs face scoring with the same storage and stop rules as _stage. A missing face is only a soft flag (added later
# by the gate), never a rejection.
def _face_stage(conn, photos: list[Photo], config: dict) -> bool:  # type: ignore[no-untyped-def]
    total = len(photos)
    for i, photo in enumerate(photos, start=1):
        if photo.status != "rejected":
            score_faces(photo, config)
        db.upsert_photo(conn, photo)
        db.set_stage(conn, photo.id, "faces", "done")
        if i % 10 == 0 or i == total:
            events.progress("faces", i, total, f"faces {i}/{total}")
        if should_stop(config):
            return True
    return False


# Groups the photos. The groups stored for exactly these inputs (see grouping_key) are reused; otherwise they are
# computed, every photo is stored with its group, and only then is the result recorded, so a recorded result always
# matches the stored photos.
def _group(conn, photos: list[Photo], config: dict) -> None:  # type: ignore[no-untyped-def]
    key = grouping_key(photos, config, conn)
    stored = db.load_stage_run(conn, "grouping", key)
    if stored is not None:
        for photo in photos:
            photo.cluster_id = int(stored.get(photo.id, -1))
        message = f"Reused {len(set(stored.values()))} unchanged clusters"
    else:
        clusters = assign_clusters(photos, config, conn)
        for photo in photos:
            db.upsert_photo(conn, photo)
        db.save_stage_run(conn, "grouping", key, {p.id: p.cluster_id for p in photos if p.cluster_id >= 0})
        message = f"Grouped into {clusters} clusters"
    events.progress("embeddings", len(photos), len(photos), message)


# Persists partial results and returns the cancellation exit code.
def _cancel(conn, config: dict) -> int:  # type: ignore[no-untyped-def]
    db.write_mirror(conn, config)
    conn.close()
    events.error("Analysis cancelled by user.")
    return 2


# Builds the argument parser with all subcommands.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AuraFrame AI local engine")
    parser.add_argument("--config", default=None, help="Path to config.yaml")

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default=None, help="Path to config.yaml")
        p.add_argument("--data-root", default=None, help="Redirect generated data under this dir")
        p.add_argument(
            "--exports-root",
            default=None,
            help="With --data-root, write the output copies and reports under this dir instead",
        )
        p.add_argument("--preset", default=None, choices=["fast", "balanced", "quality"])

    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Index a photo folder")
    p_index.add_argument("--input", required=True)
    p_index.add_argument("--no-subfolders", action="store_true")
    _common(p_index)
    p_index.set_defaults(func=cmd_index)

    p_analyze = sub.add_parser("analyze", help="Run the full pipeline")
    p_analyze.add_argument("--input", required=True)
    p_analyze.add_argument("--no-subfolders", action="store_true")
    p_analyze.add_argument("--gemma", action="store_true", help="Run local AI review")
    _common(p_analyze)
    p_analyze.set_defaults(func=cmd_analyze)

    p_gemma = sub.add_parser("gemma-review", help="Run AI review over candidates")
    p_gemma.add_argument("--limit", type=int, default=0)
    _common(p_gemma)
    p_gemma.set_defaults(func=cmd_gemma_review)

    p_export = sub.add_parser("export-final", help="Export the final set")
    p_export.add_argument("--ids", default="")
    _common(p_export)
    p_export.set_defaults(func=cmd_export_final)

    p_clear = sub.add_parser("clear-data", help="Delete all generated data")
    _common(p_clear)
    p_clear.set_defaults(func=cmd_clear_data)

    p_review = sub.add_parser("save-review", help="Persist a manual review session")
    p_review.add_argument("--file", required=True, help="JSON file with the review session")
    _common(p_review)
    p_review.set_defaults(func=cmd_save_review)

    p_load_review = sub.add_parser("load-review", help="Load the latest review session")
    _common(p_load_review)
    p_load_review.set_defaults(func=cmd_load_review)

    p_gate = sub.add_parser("local-minimal-gate", help="Run the Local Minimal Gate (no cloud)")
    p_gate.add_argument("--input", required=True)
    p_gate.add_argument("--no-subfolders", action="store_true")
    _common(p_gate)
    p_gate.set_defaults(func=cmd_local_minimal_gate)

    p_estimate = sub.add_parser("openai-estimate", help="Estimate OpenAI Quality Mode cost")
    # --input is accepted for compatibility; the SQLite DB is the authoritative source.
    p_estimate.add_argument("--input", default="")
    _common(p_estimate)
    p_estimate.set_defaults(func=cmd_openai_estimate)

    p_openai = sub.add_parser("openai-quality", help="Run optional OpenAI Quality Mode (cloud)")
    p_openai.add_argument("--input", default="")
    p_openai.add_argument(
        "--stage",
        choices=["broad_scan", "quality_review", "premium_review", "final_judge"],
        default=None,
        help="Run a single stage (default: all stages in order)",
    )
    p_openai.add_argument("--budget", type=float, default=None, help="Override the max USD budget")
    p_openai.add_argument(
        "--enable-cloud",
        action="store_true",
        help="Explicitly enable cloud review for this run (does not persist to config)",
    )
    _common(p_openai)
    p_openai.set_defaults(func=cmd_openai_quality)

    p_openai_check = sub.add_parser("openai-check", help="Probe the OpenAI API key + models")
    _common(p_openai_check)
    p_openai_check.set_defaults(func=cmd_openai_check)

    p_export_openai = sub.add_parser(
        "export-openai-results", help="Export OpenAI Quality Mode outputs + reports"
    )
    _common(p_export_openai)
    p_export_openai.set_defaults(func=cmd_export_openai_results)

    return parser


# Parses arguments and dispatches to the selected subcommand.
def main(argv: list[str] | None = None) -> int:
    # Load a repo-root .env (if present) so a file-based OPENAI_API_KEY works for the CLI.
    # Never overrides an already-set environment variable.
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - surface a structured error to the UI
        events.error(f"Engine failure: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
