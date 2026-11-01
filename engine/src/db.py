"""Local SQLite persistence for AuraFrame AI (the engine's source of truth).

All analysis state lives here so runs are resumable after a crash and unchanged photos
can be skipped on re-run. The desktop UI never reads SQLite directly: the engine mirrors
the data to photos.json via write_mirror so the Electron layer stays unchanged.

No identity data is ever stored. Image embeddings (db.embeddings) describe the whole
frame for clustering/diversity only - they are not face/identity embeddings.
"""

import json
import sqlite3
from typing import Any

import numpy as np

from .utils import Photo, db_path, write_photos_json

SCHEMA_VERSION = 1

# Ordered pipeline stages tracked in the `jobs` table for resumability. The first group is
# the local pipeline; the cloud group is only used by the optional OpenAI Quality Mode.
STAGES = (
    "ingest", "quality", "faces", "local_gate", "embeddings", "aesthetic", "vlm", "rank",
    "broad_scan", "quality_review", "premium_review", "final_judge", "consensus",
)

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
    dating_score REAL, naturalness REAL, outfit REAL, context REAL
);

CREATE TABLE IF NOT EXISTS embeddings (
    photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    model TEXT, dim INTEGER, vector BLOB
);

CREATE TABLE IF NOT EXISTS jobs (
    photo_id TEXT, stage TEXT, state TEXT, updated_at TEXT,
    PRIMARY KEY (photo_id, stage)
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


# Returns {file_path: {"id", "file_hash", "status"}} for fast skip-if-unchanged checks.
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


# Persists one fully/partly analyzed photo across the normalized tables.
def upsert_photo(conn: sqlite3.Connection, photo: Photo) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO photos("
        "id, file_path, file_name, file_hash, perceptual_hash, width, height,"
        "created_at, modified_at, exif, thumb_path, cluster_id, likely_screenshot,"
        "status, reject_reason, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
    if photo.gemma is not None:
        conn.execute(
            "INSERT OR REPLACE INTO vlm_reviews("
            "photo_id, model, raw, dating_score, naturalness, outfit, context)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                photo.id, photo.gemma.get("_model", ""),
                json.dumps(photo.gemma, ensure_ascii=False),
                float(photo.gemma.get("dating_score", 0)),
                float(photo.gemma.get("naturalness_score", 0)),
                float(photo.gemma.get("outfit_score", 0)),
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


# Loads the most recently saved manual review session for the current profile database.
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


# Loads every stored photo back into Photo objects (used by gemma-review / export).
def load_photos(conn: sqlite3.Connection) -> list[Photo]:
    photos: list[Photo] = []
    for row in conn.execute("SELECT data FROM photos").fetchall():
        photos.append(_photo_from_camel(json.loads(row["data"])))
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
            "dating_score": g.get("datingScore", 0),
            "technical_quality": g.get("technicalQuality", 0),
            "face_clarity": g.get("faceClarity", 0),
            "expression_score": g.get("expressionScore", 0),
            "naturalness_score": g.get("naturalnessScore", 0),
            "outfit_score": g.get("outfitScore", 0),
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
