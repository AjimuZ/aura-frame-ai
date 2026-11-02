"""Shared helpers for the AuraFrame AI engine: config, paths, discovery, control flow.

All paths resolve relative to the repository root so nothing user-specific is hardcoded.
A `--data-root` override (see apply_data_root) lets the desktop app redirect every
generated artifact under its own userData directory without editing config on disk.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from PIL import Image

# Register the HEIF/HEIC opener so Pillow (and our OpenCV fallback) can read .heic
# files. Optional: if pillow-heif is unavailable, HEIC files are simply skipped.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001 - HEIC support is optional
    pass


# Returns the repository root (two levels above this file: engine/src/ -> repo).
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Loads the YAML engine configuration, falling back to engine/config.yaml.
def load_config(config_path: str | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else repo_root() / "engine" / "config.yaml"
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# Loads simple KEY=VALUE lines from a repository-root `.env` into the process environment,
# without overriding variables that are already set (so the shell/UI always wins). This is
# the file-based way to provide secrets such as OPENAI_API_KEY; `.env` is git-ignored and its
# values are never logged. A missing file is a no-op.
def load_dotenv(path: Path | None = None) -> None:
    env_path = path or (repo_root() / ".env")
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Rewrites every generated-artifact path under a single root (the desktop userData dir).
# Originals are never touched; only working/thumbs/db/output/reports are redirected.
# With `exports_root`, the copies and reports (`output`, `reports`) go under that root instead, so
# the API container can keep them in the folder the person opens while `working` stays in its volume.
def apply_data_root(
    config: dict[str, Any], data_root: str | None, exports_root: str | None = None
) -> dict[str, Any]:
    if not data_root:
        return config
    root = Path(data_root)
    shown = Path(exports_root) if exports_root else root
    out = config["output"]
    out["working_dir"] = str(root / "working")
    out["thumbs_dir"] = str(root / "working" / "thumbs")
    out["db_path"] = str(root / "working" / "auraframe.db")
    out["base_dir"] = str(shown / "output")
    out["reports_dir"] = str(shown / "reports")
    return config


# Applies a named preset (fast|balanced|quality), overriding the affected config keys.
def apply_preset(config: dict[str, Any], preset: str | None) -> dict[str, Any]:
    if not preset:
        return config
    presets = config.get("presets", {})
    chosen = presets.get(preset)
    if not chosen:
        return config
    config.setdefault("_active_preset", preset)
    if "vlm_candidate_count" in chosen:
        config["output"]["vlm_candidate_count"] = chosen["vlm_candidate_count"]
    config["embeddings"]["enabled"] = bool(chosen.get("embeddings", config["embeddings"]["enabled"]))
    config["aesthetic"]["enabled"] = bool(chosen.get("aesthetic", config["aesthetic"]["enabled"]))
    config["_vlm_enabled"] = bool(chosen.get("vlm", True))
    return config


# Resolves a possibly-relative config path against the repository root.
def resolve_path(value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (repo_root() / candidate)


# Ensures a directory exists and returns it.
def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# Returns the working directory used for the index, thumbnails, and control file.
def working_dir(config: dict[str, Any]) -> Path:
    return ensure_dir(resolve_path(config["output"].get("working_dir", "./data/working")))


# Returns the thumbnail cache directory (configurable; under userData when redirected).
def thumbs_dir(config: dict[str, Any]) -> Path:
    default = str(working_dir(config) / "thumbs")
    return ensure_dir(resolve_path(config["output"].get("thumbs_dir", default)))


# Returns the absolute path of the local SQLite database (source of truth).
def db_path(config: dict[str, Any]) -> Path:
    default = str(working_dir(config) / "auraframe.db")
    path = resolve_path(config["output"].get("db_path", default))
    ensure_dir(path.parent)
    return path


# Returns the absolute path of the JSON file holding the analyzed photo list (UI mirror).
def photos_json_path(config: dict[str, Any]) -> Path:
    return working_dir(config) / "photos.json"


# Returns the path of the pause/cancel control file.
def control_file_path(config: dict[str, Any]) -> Path:
    return working_dir(config) / "control.json"


# Reads the control file; returns paused/cancel flags, defaulting to running.
def read_control(config: dict[str, Any]) -> tuple[bool, bool]:
    try:
        data = json.loads(control_file_path(config).read_text(encoding="utf-8"))
        return bool(data.get("paused", False)), bool(data.get("cancel", False))
    except (OSError, json.JSONDecodeError):
        return False, False


# Blocks while paused; returns True if the run should stop (cancelled).
def should_stop(config: dict[str, Any]) -> bool:
    paused, cancel = read_control(config)
    while paused and not cancel:
        time.sleep(0.4)
        paused, cancel = read_control(config)
    return cancel


# Masks an absolute path to just its filename for privacy-safe logs (when enabled).
def mask_path(value: str, config: dict[str, Any]) -> str:
    if config.get("privacy", {}).get("mask_paths_in_logs", True):
        try:
            return Path(value).name
        except Exception:  # noqa: BLE001 - logging must never raise
            return "<file>"
    return value


# Recursively discovers supported image files under a folder.
def discover_images(folder: Path, extensions: list[str], include_subfolders: bool) -> list[Path]:
    exts = {e.lower() for e in extensions}
    if include_subfolders:
        candidates = folder.rglob("*")
    else:
        candidates = folder.glob("*")
    return sorted(p for p in candidates if p.is_file() and p.suffix.lower() in exts)


# Reads an image as a BGR numpy array, falling back to Pillow for formats OpenCV cannot
# decode directly (e.g. HEIC). Returns None if the image cannot be read.
def read_image_bgr(path: str) -> np.ndarray | None:
    image = cv2.imread(path)
    if image is not None:
        return image
    try:
        with Image.open(path) as pil:
            rgb = pil.convert("RGB")
            return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
    except Exception:  # noqa: BLE001 - unreadable images are handled by callers
        return None


# Computes the SHA-256 of a file's bytes as 64 lower-case hex digits. The file is opened
# read-only and streamed in chunks, so large originals never load fully into memory.
def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Returns the photo ID of a file: the SHA-256 of its content. The ID does not depend on the
# path or the name, so a moved or renamed file keeps its results and byte-identical copies
# share one ID. Raises OSError when the file cannot be read.
def photo_id_for(path: str | Path) -> str:
    return file_sha256(path)


# Returns the directory of the upright working copies (under the working directory).
def working_copies_dir(config: dict[str, Any]) -> Path:
    return working_dir(config) / "copies"


# Returns where the working copy of a photo lives. Nothing is created. The copy is the only
# image the scoring stages read, so each original is decoded once, at ingest.
def working_copy_path(config: dict[str, Any], photo_id: str) -> Path:
    return working_copies_dir(config) / f"{photo_id}.jpg"


# Returns where the thumbnail of a photo lives. Nothing is created.
def thumbnail_path_for(config: dict[str, Any], photo_id: str) -> Path:
    return thumbs_dir(config) / f"{photo_id}.jpg"


# Reads the upright working copy of a photo as a BGR numpy array. Returns None when the copy
# is missing or unreadable. The copy carries no orientation tag, so no stage rotates it again.
def read_working_bgr(photo: "Photo", config: dict[str, Any]) -> np.ndarray | None:
    path = working_copy_path(config, photo.id)
    if not path.is_file():
        return None
    return cv2.imread(str(path))


# Writes the full photo list as JSON for the desktop app to read (mirror of the DB).
def write_photos_json(config: dict[str, Any], photos: list[dict[str, Any]]) -> None:
    photos_json_path(config).write_text(
        json.dumps(photos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# Reads the previously written photo list, or an empty list if absent.
def read_photos_json(config: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return json.loads(photos_json_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


@dataclass
class Photo:
    """In-memory representation of one analyzed photo (serialized to camelCase JSON)."""

    id: str
    file_path: str
    file_name: str
    thumbnail_path: str = ""
    width: int = 0
    height: int = 0
    created_at: str | None = None
    modified_at: str | None = None
    file_hash: str = ""
    perceptual_hash: str = ""
    exif: dict[str, Any] = field(default_factory=dict)
    technical_score: float = 0.0
    blur_score: float = 0.0
    brightness_score: float = 0.0
    contrast_score: float = 0.0
    resolution_score: float = 0.0
    likely_screenshot: bool = False
    face_detected: bool = False
    number_of_faces: int = 0
    face_clarity_score: float = 0.0
    face_size_score: float = 0.0
    face_center_score: float = 0.0
    eyes_visible_score: float = 0.0
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    occlusion_score: float = 0.0
    group_photo: bool = False
    face_quality_score: float = 0.0
    # 0-1 deduction for closed eyes or a blurred main face, from the Face Landmarker; 0 when no face is found.
    people_penalty: float = 0.0
    aesthetic_score: float = 0.0
    cluster_id: int = -1
    gemma: dict[str, Any] | None = None
    final_score: float = 0.0
    category: str = "REJECT"
    best_slot: str = "reject"
    status: str = "pending"
    reject_reason: str | None = None
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    reason: str = ""

    # --- Local Minimal Gate (privacy-first triage; does NOT decide photo quality) ---
    soft_flags: list[str] = field(default_factory=list)
    gate_status: str = ""  # openai_candidate|low_priority_openai_candidate|needs_manual_review|duplicate_candidate|hard_reject|failed_read
    hard_reject_reason: str = ""
    eligible_for_openai: bool = False
    openai_priority: int = 0  # 1=highest .. 4=lowest; 0=not eligible
    is_near_duplicate: bool = False
    # Raw metrics used only at gate time (transient; intentionally not serialized).
    brightness_mean: float = 0.0
    laplacian_variance: float = 0.0
    # True when ingest reused a stored record for unchanged content instead of decoding the
    # file again. Transient: never serialized or persisted.
    carried_over: bool = False

    # --- OpenAI Quality Mode results (only set when the optional cloud flow runs) ---
    openai_broad: dict[str, Any] | None = None
    openai_quality: dict[str, Any] | None = None
    openai_premium: dict[str, Any] | None = None
    openai_broad_score: float = 0.0
    openai_quality_score: float = 0.0
    openai_premium_score: float = 0.0
    final_judge_score: float = 0.0
    consensus_score: float = 0.0
    openai_reason: str = ""
    openai_risk: str = ""
    needs_manual_review: bool = False
    failed_openai_review: bool = False
    in_top_6: bool = False
    in_top_20: bool = False
    in_top_50: bool = False
    in_top_100: bool = False
    final_selected_slot: str = ""
    estimated_cost_usd: float = 0.0

    # Serializes the photo into the camelCase shape expected by the Angular UI.
    def to_camel(self) -> dict[str, Any]:
        gemma_camel = None
        if self.gemma is not None:
            gemma_camel = {
                "overallScore": self.gemma.get("overall_score", 0),
                "technicalQuality": self.gemma.get("technical_quality", 0),
                "faceClarity": self.gemma.get("face_clarity", 0),
                "expressionScore": self.gemma.get("expression_score", 0),
                "naturalnessScore": self.gemma.get("naturalness_score", 0),
                "styleScore": self.gemma.get("style_score", 0),
                "contextScore": self.gemma.get("context_score", 0),
                "category": self.gemma.get("category", self.category),
                "bestSlot": self.gemma.get("best_slot", self.best_slot),
                "strengths": self.gemma.get("strengths", []),
                "weaknesses": self.gemma.get("weaknesses", []),
                "reason": self.gemma.get("reason", ""),
            }
        return {
            "id": self.id,
            "filePath": self.file_path,
            "fileName": self.file_name,
            "thumbnailPath": self.thumbnail_path,
            "width": self.width,
            "height": self.height,
            "createdAt": self.created_at,
            "modifiedAt": self.modified_at,
            "technicalScore": round(self.technical_score, 1),
            "blurScore": round(self.blur_score, 1),
            "brightnessScore": round(self.brightness_score, 1),
            "contrastScore": round(self.contrast_score, 1),
            "likelyScreenshot": self.likely_screenshot,
            "faceDetected": self.face_detected,
            "faceClarityScore": round(self.face_clarity_score, 1),
            "faceSizeScore": round(self.face_size_score, 1),
            "faceCenterScore": round(self.face_center_score, 1),
            "eyesVisibleScore": round(self.eyes_visible_score, 1),
            "headYaw": round(self.head_yaw, 1),
            "headPitch": round(self.head_pitch, 1),
            "occlusionScore": round(self.occlusion_score, 1),
            "groupPhoto": self.group_photo,
            "faceQualityScore": round(self.face_quality_score, 1),
            "peoplePenalty": round(self.people_penalty, 3),
            "aestheticScore": round(self.aesthetic_score, 1),
            "clusterId": self.cluster_id,
            "numberOfFaces": self.number_of_faces,
            "gemmaScore": gemma_camel,
            "finalScore": round(self.final_score, 2),
            "category": self.category,
            "bestSlot": self.best_slot,
            "status": self.status,
            "rejectReason": self.reject_reason,
            "softFlags": list(self.soft_flags),
            "gateStatus": self.gate_status,
            "hardRejectReason": self.hard_reject_reason,
            "eligibleForOpenai": self.eligible_for_openai,
            "openaiPriority": self.openai_priority,
            "isNearDuplicate": self.is_near_duplicate,
            "openaiBroad": self.openai_broad,
            "openaiQuality": self.openai_quality,
            "openaiPremium": self.openai_premium,
            "openaiBroadScore": round(self.openai_broad_score, 2),
            "openaiQualityScore": round(self.openai_quality_score, 2),
            "openaiPremiumScore": round(self.openai_premium_score, 2),
            "finalJudgeScore": round(self.final_judge_score, 2),
            "consensusScore": round(self.consensus_score, 2),
            "openaiReason": self.openai_reason,
            "openaiRisk": self.openai_risk,
            "needsManualReview": self.needs_manual_review,
            "failedOpenaiReview": self.failed_openai_review,
            "inTop6": self.in_top_6,
            "inTop20": self.in_top_20,
            "inTop50": self.in_top_50,
            "inTop100": self.in_top_100,
            "finalSelectedSlot": self.final_selected_slot,
            "estimatedCostUsd": round(self.estimated_cost_usd, 6),
            "reasonTags": self.reason_tags(),
        }

    # Derives short, human-readable explainability tags from the computed metrics.
    def reason_tags(self) -> list[str]:
        tags: list[str] = []
        if self.face_clarity_score >= 70:
            tags.append("sharp face")
        if self.brightness_score >= 75 and self.contrast_score >= 55:
            tags.append("natural light")
        if self.brightness_score < 45:
            tags.append("low light")
        if self.group_photo:
            tags.append("group photo")
        if self.likely_screenshot:
            tags.append("screenshot")
        if self.aesthetic_score >= 65:
            tags.append("strong aesthetic")
        if self.reject_reason in {"duplicate_similar", "duplicate_exact"}:
            tags.append("too similar")
        return tags
