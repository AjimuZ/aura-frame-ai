"""Local SQLite persistence for AuraFrame AI (the engine's source of truth).

All analysis state lives here so runs are resumable after a crash and unchanged photos
can be skipped on re-run. The desktop UI never reads SQLite directly: the engine mirrors
the data to photos.json via write_mirror so the Electron layer stays unchanged.

No identity data is ever stored. Image embeddings (db.embeddings) describe the whole
frame for clustering/diversity only - they are not face/identity embeddings.
"""

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

import numpy as np

from .utils import Photo, db_path, write_photos_json

SCHEMA_VERSION = 1

# Ordered pipeline stages tracked in the `jobs` table for resumability. The first group is
# the local pipeline; the cloud group is only used by the optional OpenAI Quality Mode.
STAGES = (
    "ingest", "quality", "faces", "local_gate", "qrealign", "embeddings", "content_tags", "aesthetic", "vlm",
    "rank", "broad_scan", "quality_review", "premium_review", "final_judge", "consensus",
)  # fmt: skip

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_name TEXT,
    file_hash TEXT,
    perceptual_hash TEXT,
    width INTEGER, height INTEGER,
    created_at TEXT, modified_at TEXT,
    exif TEXT,
    thumb_path TEXT,
    cluster_id INTEGER DEFAULT -1,
    likely_screenshot INTEGER DEFAULT 0,
    status TEXT, reject_reason TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_photos_hash ON photos(file_hash);
CREATE INDEX IF NOT EXISTS idx_photos_path ON photos(file_path);

CREATE TABLE IF NOT EXISTS scores (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    technical REAL, blur REAL, brightness REAL, contrast REAL, resolution REAL,
    aesthetic REAL, face_quality REAL, final_score REAL,
    category TEXT, best_slot TEXT
);

CREATE TABLE IF NOT EXISTS faces (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    num_faces INTEGER, size_score REAL, center_score REAL, clarity_score REAL,
    eyes_score REAL, head_yaw REAL, head_pitch REAL, occlusion REAL, group_flag INTEGER
);

CREATE TABLE IF NOT EXISTS vlm_reviews (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    model TEXT, raw TEXT,
    overall_score REAL, naturalness REAL, style REAL, context REAL
);

CREATE TABLE IF NOT EXISTS embeddings (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    model TEXT, dim INTEGER, vector BLOB
);

-- Q-ReAlign values between 0 and 1. NULL means unknown (model missing, photo unreadable); never a neutral number.
CREATE TABLE IF NOT EXISTS neural_scores (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    model TEXT, quality REAL, aesthetic REAL
);

-- One zero-shot content tag per photo, from the fixed list in content_tags.py; score is the cosine similarity.
CREATE TABLE IF NOT EXISTS content_tags (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    tag TEXT NOT NULL, score REAL, model TEXT
);

-- Cached judge verdicts, one per ordered pair. first_id/second_id are photo IDs (content hashes) in the order the
-- judge saw them, so the cache survives a re-ingest. No path, file name or image data is stored.
CREATE TABLE IF NOT EXISTS verdicts (
    first_id TEXT NOT NULL, second_id TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
    winner TEXT NOT NULL, confidence TEXT, reasons TEXT, note TEXT,
    PRIMARY KEY (first_id, second_id, provider, model, prompt_version)
);

CREATE TABLE IF NOT EXISTS jobs (
    photo_id TEXT, stage TEXT, state TEXT, updated_at TEXT,
    PRIMARY KEY (photo_id, stage)
);

-- Exact values the camelCase record rounds or leaves out, so a resumed photo ranks and gates like a fresh one.
CREATE TABLE IF NOT EXISTS photo_measures (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    brightness_mean REAL, laplacian_variance REAL, people_penalty REAL
);

-- Stages that work on the whole collection (grouping): the key is a hash of every input, the result is JSON.
CREATE TABLE IF NOT EXISTS stage_runs (
    stage TEXT PRIMARY KEY, input_key TEXT NOT NULL, result TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS review_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    slot_assignments TEXT, locks TEXT, shortlist TEXT
);
"""


# Opens (or creates) the local database with safe pragmas and the current schema.
def connect(config: dict[str, Any]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path(config)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


# Creates tables if absent and records the schema version (idempotent).
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


# Returns {file_path: {"id", "file_hash", "status"}} (the id is the content hash).
def existing_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT id, file_path, file_hash, status FROM photos").fetchall()
    return {r["file_path"]: dict(r) for r in rows}


# Marks a pipeline stage for one photo as done|pending|error (resumability bookkeeping).
def set_stage(conn: sqlite3.Connection, photo_id: str, stage: str, state: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO jobs(photo_id, stage, state, updated_at) "
        "VALUES(?, ?, ?, datetime('now'))",
        (photo_id, stage, state),
    )
    conn.commit()


# Returns the recorded state of a stage for a photo (or 'pending' if never run).
def get_stage(conn: sqlite3.Connection, photo_id: str, stage: str) -> str:
    row = conn.execute(
        "SELECT state FROM jobs WHERE photo_id = ? AND stage = ?", (photo_id, stage)
    ).fetchone()
    return row["state"] if row else "pending"


# Marks one stage as `state` for many photos in a single transaction, so a batch is recorded whole or not at all.
# The caller writes the stage's results first: a photo marked "done" always has its data stored.
def set_stages(conn: sqlite3.Connection, photo_ids: Iterable[str], stage: str, state: str) -> None:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO jobs(photo_id, stage, state, updated_at) VALUES(?, ?, ?, datetime('now'))",
            [(photo_id, stage, state) for photo_id in photo_ids],
        )


# Returns the IDs of the photos whose `stage` is recorded as done.
def done_ids(conn: sqlite3.Connection, stage: str) -> set[str]:
    rows = conn.execute("SELECT photo_id FROM jobs WHERE stage = ? AND state = 'done'", (stage,)).fetchall()
    return {row["photo_id"] for row in rows}


# Forgets every stage state of one photo, so each stage runs on it again. Used when a photo is ingested anew and
# its stored results no longer describe what is in memory.
def clear_stages(conn: sqlite3.Connection, photo_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM jobs WHERE photo_id = ?", (photo_id,))


# Stores the result of a collection-level stage under the hash of its inputs, replacing the earlier one.
def save_stage_run(conn: sqlite3.Connection, stage: str, input_key: str, result: Any) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO stage_runs(stage, input_key, result, updated_at) VALUES(?, ?, ?, datetime('now'))",
            (stage, input_key, json.dumps(result, ensure_ascii=False)),
        )


# Returns the stored result of a collection-level stage when it was computed from exactly `input_key`, else None.
def load_stage_run(conn: sqlite3.Connection, stage: str, input_key: str) -> Any | None:
    row = conn.execute(
        "SELECT result FROM stage_runs WHERE stage = ? AND input_key = ?", (stage, input_key)
    ).fetchone()
    return None if row is None else json.loads(row["result"])


# Removes every other record stored for the same file path. A photo ID is the content hash, so
# when the bytes at a path change (or the record predates content IDs) the old record describes
# content that is no longer there; the cascade drops its scores, faces and embedding with it.
def _drop_superseded(conn: sqlite3.Connection, photo: Photo) -> None:
    stale = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM photos WHERE file_path = ? AND id != ?", (photo.file_path, photo.id)
        ).fetchall()
    ]
    for photo_id in stale:
        conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        conn.execute("DELETE FROM jobs WHERE photo_id = ?", (photo_id,))


_PHOTO_COLUMNS = (
    "id", "file_path", "file_name", "file_hash", "perceptual_hash", "width", "height", "created_at", "modified_at",
    "exif", "thumb_path", "cluster_id", "likely_screenshot", "status", "reject_reason", "data",
)  # fmt: skip

# Updates the row in place on a known ID. INSERT OR REPLACE would delete it first, and with foreign keys on that
# delete cascades to the photo's Q-ReAlign scores, embedding and content tag, losing finished work.
_UPSERT_PHOTO = (
    f"INSERT INTO photos({', '.join(_PHOTO_COLUMNS)}) VALUES ({', '.join(['?'] * len(_PHOTO_COLUMNS))})"
    f" ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in _PHOTO_COLUMNS[1:])}"
)


# Persists one fully/partly analyzed photo across the normalized tables. Records that the same
# path held before under another ID (changed content, or an older ID scheme) are replaced. The
# photo's rows in neural_scores, embeddings and content_tags are kept.
def upsert_photo(conn: sqlite3.Connection, photo: Photo) -> None:
    _drop_superseded(conn, photo)
    conn.execute(
        _UPSERT_PHOTO,
        (
            photo.id, photo.file_path, photo.file_name, photo.file_hash,
            photo.perceptual_hash, photo.width, photo.height,
            photo.created_at, photo.modified_at, json.dumps(photo.exif, ensure_ascii=False),
            photo.thumbnail_path, photo.cluster_id, int(photo.likely_screenshot),
            photo.status, photo.reject_reason,
            json.dumps(photo.to_camel(), ensure_ascii=False),
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO scores("
        "photo_id, technical, blur, brightness, contrast, resolution, aesthetic,"
        "face_quality, final_score, category, best_slot) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            photo.id, photo.technical_score, photo.blur_score, photo.brightness_score,
            photo.contrast_score, photo.resolution_score, photo.aesthetic_score,
            photo.face_quality_score, photo.final_score, photo.category, photo.best_slot,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO faces("
        "photo_id, num_faces, size_score, center_score, clarity_score, eyes_score,"
        "head_yaw, head_pitch, occlusion, group_flag) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            photo.id, photo.number_of_faces, photo.face_size_score, photo.face_center_score,
            photo.face_clarity_score, photo.eyes_visible_score, photo.head_yaw,
            photo.head_pitch, photo.occlusion_score, int(photo.group_photo),
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO photo_measures(photo_id, brightness_mean, laplacian_variance, people_penalty)"
        " VALUES (?,?,?,?)",
        (photo.id, photo.brightness_mean, photo.laplacian_variance, photo.people_penalty),
    )
    if photo.gemma is not None:
        conn.execute(
            "INSERT OR REPLACE INTO vlm_reviews("
            "photo_id, model, raw, overall_score, naturalness, style, context)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                photo.id, photo.gemma.get("_model", ""),
                json.dumps(photo.gemma, ensure_ascii=False),
                float(photo.gemma.get("overall_score", 0)),
                float(photo.gemma.get("naturalness_score", 0)),
                float(photo.gemma.get("style_score", 0)),
                float(photo.gemma.get("context_score", 0)),
            ),
        )
    conn.commit()


# Stores a whole-frame embedding (float32) for clustering/diversity (never identity).
def save_embedding(conn: sqlite3.Connection, photo_id: str, vector: np.ndarray, model: str) -> None:
    vec = np.asarray(vector, dtype=np.float32)
    conn.execute(
        "INSERT OR REPLACE INTO embeddings(photo_id, model, dim, vector) VALUES (?,?,?,?)",
        (photo_id, model, int(vec.size), vec.tobytes()),
    )
    conn.commit()


# Loads all stored embeddings as {photo_id: float32 vector}.
def load_embeddings(conn: sqlite3.Connection) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for row in conn.execute("SELECT photo_id, vector FROM embeddings").fetchall():
        # SQLite returns immutable bytes. Copy the view so consumers such as
        # torch.from_numpy receive a writable array and can safely share its memory.
        out[row["photo_id"]] = np.frombuffer(row["vector"], dtype=np.float32).copy()
    return out


# Stores the Q-ReAlign quality and aesthetic values of a photo; None means unknown and is stored as NULL.
# Raises ValueError for a value that is not a finite number between 0 and 1, so no out-of-range score is kept.
def save_neural_scores(
    conn: sqlite3.Connection, photo_id: str, model: str, quality: float | None, aesthetic: float | None
) -> None:
    for value in (quality, aesthetic):
        if value is not None and not 0.0 <= float(value) <= 1.0:  # also false for NaN
            raise ValueError("a Q-ReAlign value must be between 0 and 1")
    stored = tuple(None if value is None else float(value) for value in (quality, aesthetic))
    conn.execute(
        "INSERT OR REPLACE INTO neural_scores(photo_id, model, quality, aesthetic) VALUES (?,?,?,?)",
        (photo_id, model, *stored),
    )
    conn.commit()


# Loads every stored Q-ReAlign row as {photo_id: {"model", "quality", "aesthetic"}}; a value is None when unknown.
def load_neural_scores(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT photo_id, model, quality, aesthetic FROM neural_scores").fetchall()
    return {r["photo_id"]: {"model": r["model"], "quality": r["quality"], "aesthetic": r["aesthetic"]} for r in rows}


# Stores the content tag of a photo, replacing any earlier one. The caller validates the tag against the fixed list.
def save_content_tag(conn: sqlite3.Connection, photo_id: str, tag: str, score: float, model: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO content_tags(photo_id, tag, score, model) VALUES (?,?,?,?)",
        (photo_id, tag, float(score), model),
    )
    conn.commit()


# Loads every stored content tag as {photo_id: {"tag", "score", "model"}}.
def load_content_tags(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT photo_id, tag, score, model FROM content_tags").fetchall()
    return {r["photo_id"]: {"tag": r["tag"], "score": r["score"], "model": r["model"]} for r in rows}


# Stores the verdict of one ordered pair under its full cache key, replacing an earlier one. Raises ValueError
# for a winner other than "A", "B" or "tie", so a malformed verdict is never cached.
def save_verdict(
    conn: sqlite3.Connection,
    key: tuple[str, str, str, str, str],
    winner: str,
    confidence: str | None,
    reasons: tuple[str, ...],
    note: str,
) -> None:
    if winner not in ("A", "B", "tie"):
        raise ValueError("a cached verdict must be A, B or tie")
    conn.execute(
        "INSERT OR REPLACE INTO verdicts("
        "first_id, second_id, provider, model, prompt_version, winner, confidence, reasons, note)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (*key, winner, confidence, json.dumps(list(reasons), ensure_ascii=False), note),
    )
    conn.commit()


# Loads the cached verdict for a key (first_id, second_id, provider, model, prompt_version) as
# {"winner", "confidence", "reasons", "note"}, or None when this exact combination was never judged.
def load_verdict(conn: sqlite3.Connection, key: tuple[str, str, str, str, str]) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT winner, confidence, reasons, note FROM verdicts WHERE first_id = ? AND second_id = ?"
        " AND provider = ? AND model = ? AND prompt_version = ?",
        key,
    ).fetchone()
    if row is None:
        return None
    return {
        "winner": row["winner"],
        "confidence": row["confidence"],
        "reasons": tuple(json.loads(row["reasons"] or "[]")),
        "note": row["note"] or "",
    }


# Persists a manual review session (slot assignments, per-slot locks, shortlist).
def save_review_session(
    conn: sqlite3.Connection,
    slot_assignments: dict[str, Any],
    locks: dict[str, Any],
    shortlist: list[str],
) -> int:
    cur = conn.execute(
        "INSERT INTO review_sessions(created_at, slot_assignments, locks, shortlist)"
        " VALUES (datetime('now'), ?, ?, ?)",
        (
            json.dumps(slot_assignments, ensure_ascii=False),
            json.dumps(locks, ensure_ascii=False),
            json.dumps(shortlist, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


# Loads the most recently saved manual review session for the current library database.
def load_latest_review_session(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT slot_assignments, locks, shortlist FROM review_sessions "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "slotAssignments": json.loads(row["slot_assignments"] or "{}"),
        "locks": json.loads(row["locks"] or "{}"),
        "shortlist": json.loads(row["shortlist"] or "[]"),
    }


# Columns of the normalized tables that hold the exact value of a Photo attribute the camelCase record rounds or
# omits, as (SQL expression, attribute).
_EXACT_COLUMNS = (
    ("s.technical", "technical_score"), ("s.blur", "blur_score"), ("s.brightness", "brightness_score"),
    ("s.contrast", "contrast_score"), ("s.resolution", "resolution_score"), ("s.aesthetic", "aesthetic_score"),
    ("s.face_quality", "face_quality_score"), ("f.size_score", "face_size_score"),
    ("f.center_score", "face_center_score"), ("f.clarity_score", "face_clarity_score"),
    ("f.eyes_score", "eyes_visible_score"), ("f.head_yaw", "head_yaw"), ("f.head_pitch", "head_pitch"),
    ("f.occlusion", "occlusion_score"), ("m.brightness_mean", "brightness_mean"),
    ("m.laplacian_variance", "laplacian_variance"), ("m.people_penalty", "people_penalty"),
)  # fmt: skip


# Loads every stored photo back into Photo objects, in insertion order. The file hash, perceptual hash, EXIF subset
# and the exact scores come from their own columns, so a reused photo is identical to the one that was stored and a
# resumed run gates, groups and ranks it exactly as an uninterrupted run would. A missing column value (a record
# from before a table existed) keeps what the camelCase record holds.
def load_photos(conn: sqlite3.Connection) -> list[Photo]:
    columns = ", ".join(f"{expression} AS {attribute}" for expression, attribute in _EXACT_COLUMNS)
    rows = conn.execute(
        f"SELECT p.data, p.file_hash, p.perceptual_hash, p.exif, {columns} FROM photos p"
        " LEFT JOIN scores s ON s.photo_id = p.id LEFT JOIN faces f ON f.photo_id = p.id"
        " LEFT JOIN photo_measures m ON m.photo_id = p.id ORDER BY p.rowid"
    ).fetchall()
    photos: list[Photo] = []
    for row in rows:
        photo = _photo_from_camel(json.loads(row["data"]))
        photo.file_hash = row["file_hash"] or ""
        photo.perceptual_hash = row["perceptual_hash"] or ""
        photo.exif = json.loads(row["exif"] or "{}")
        for _, attribute in _EXACT_COLUMNS:
            if row[attribute] is not None:
                setattr(photo, attribute, float(row[attribute]))
        photos.append(photo)
    return photos


# Mirrors the current photo set to photos.json so the desktop app can read it unchanged.
def write_mirror(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    records = [json.loads(r["data"]) for r in conn.execute("SELECT data FROM photos").fetchall()]
    write_photos_json(config, records)


# Reconstructs a Photo from a persisted camelCase record (best-effort, tolerant).
def _photo_from_camel(record: dict[str, Any]) -> Photo:
    gemma = None
    g = record.get("gemmaScore")
    if g:
        gemma = {
            "overall_score": g.get("overallScore", 0),
            "technical_quality": g.get("technicalQuality", 0),
            "face_clarity": g.get("faceClarity", 0),
            "expression_score": g.get("expressionScore", 0),
            "naturalness_score": g.get("naturalnessScore", 0),
            "style_score": g.get("styleScore", 0),
            "context_score": g.get("contextScore", 0),
            "category": g.get("category", record.get("category", "REJECT")),
            "best_slot": g.get("bestSlot", record.get("bestSlot", "reject")),
            "strengths": g.get("strengths", []),
            "weaknesses": g.get("weaknesses", []),
            "reason": g.get("reason", ""),
        }
    return Photo(
        id=record["id"],
        file_path=record["filePath"],
        file_name=record["fileName"],
        thumbnail_path=record.get("thumbnailPath", ""),
        width=record.get("width", 0),
        height=record.get("height", 0),
        created_at=record.get("createdAt"),
        modified_at=record.get("modifiedAt"),
        technical_score=record.get("technicalScore", 0.0),
        blur_score=record.get("blurScore", 0.0),
        brightness_score=record.get("brightnessScore", 0.0),
        contrast_score=record.get("contrastScore", 0.0),
        likely_screenshot=record.get("likelyScreenshot", False),
        face_detected=record.get("faceDetected", False),
        number_of_faces=record.get("numberOfFaces", 0),
        face_clarity_score=record.get("faceClarityScore", 0.0),
        face_size_score=record.get("faceSizeScore", 0.0),
        face_center_score=record.get("faceCenterScore", 0.0),
        eyes_visible_score=record.get("eyesVisibleScore", 0.0),
        head_yaw=record.get("headYaw", 0.0),
        head_pitch=record.get("headPitch", 0.0),
        occlusion_score=record.get("occlusionScore", 0.0),
        group_photo=record.get("groupPhoto", False),
        face_quality_score=record.get("faceQualityScore", 0.0),
        people_penalty=record.get("peoplePenalty", 0.0),
        aesthetic_score=record.get("aestheticScore", 0.0),
        cluster_id=record.get("clusterId", -1),
        gemma=gemma,
        final_score=record.get("finalScore", 0.0),
        category=record.get("category", "REJECT"),
        best_slot=record.get("bestSlot", "reject"),
        status=record.get("status", "pending"),
        reject_reason=record.get("rejectReason"),
        soft_flags=list(record.get("softFlags", []) or []),
        gate_status=record.get("gateStatus", ""),
        hard_reject_reason=record.get("hardRejectReason", ""),
        eligible_for_openai=record.get("eligibleForOpenai", False),
        openai_priority=record.get("openaiPriority", 0),
        is_near_duplicate=record.get("isNearDuplicate", False),
        openai_broad=record.get("openaiBroad"),
        openai_quality=record.get("openaiQuality"),
        openai_premium=record.get("openaiPremium"),
        openai_broad_score=record.get("openaiBroadScore", 0.0),
        openai_quality_score=record.get("openaiQualityScore", 0.0),
        openai_premium_score=record.get("openaiPremiumScore", 0.0),
        final_judge_score=record.get("finalJudgeScore", 0.0),
        consensus_score=record.get("consensusScore", 0.0),
        openai_reason=record.get("openaiReason", ""),
        openai_risk=record.get("openaiRisk", ""),
        needs_manual_review=record.get("needsManualReview", False),
        failed_openai_review=record.get("failedOpenaiReview", False),
        in_top_6=record.get("inTop6", False),
        in_top_20=record.get("inTop20", False),
        in_top_50=record.get("inTop50", False),
        in_top_100=record.get("inTop100", False),
        final_selected_slot=record.get("finalSelectedSlot", ""),
        estimated_cost_usd=record.get("estimatedCostUsd", 0.0),
    )
