"""Tests for the sanity gate policy and OpenAI Quality Mode (no real API calls).

All images are synthetic and all OpenAI interactions are faked. These tests assert the
privacy-critical behaviors (aspect-preserving resize, no forced square, EXIF removal, temp
deletion), the minimal-gate policy, JSON parsing/recovery, the budget guard, the cache, and
consensus ranking.
"""

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image

from src.cloud import consensus_ranker as cr
from src.cloud import openai_cache as cache
from src.cloud import openai_cost_estimator as ce
from src.cloud import openai_result_parser as parser
from src.cloud.openai_budget_guard import BudgetGuard
from src.cloud.openai_image_preparer import prepare_image, temp_dir
from src.cloud.openai_photo_judge import run_stage
from src.local_gate import apply_local_minimal_gate
from src.utils import Photo


# Builds a Photo with measured metrics already set (so the gate can run without an image).
def _measured(**kwargs) -> Photo:
    base = dict(
        id="p", file_path="x.jpg", file_name="x.jpg", width=1200, height=1500,
        brightness_mean=120.0, laplacian_variance=400.0, technical_score=70.0,
        face_detected=True, face_size_score=50.0,
    )
    base.update(kwargs)
    return Photo(**base)


# --- Local Minimal Gate ---------------------------------------------------------------

# Brightness never rejects: a black frame and a moderately dark one are only soft-flagged.
def test_gate_keeps_black_and_dark(config: dict) -> None:
    for mean in (4.0, 20.0):
        photo = _measured(brightness_mean=mean)
        apply_local_minimal_gate(photo, config)
        assert photo.gate_status != "hard_reject"
        assert photo.status != "rejected"
        assert photo.eligible_for_openai
        assert "local_flag_low_light" in photo.soft_flags
    bright = _measured(brightness_mean=252.0)
    apply_local_minimal_gate(bright, config)
    assert bright.gate_status != "hard_reject" and bright.eligible_for_openai
    assert "local_flag_low_light" in bright.soft_flags


# A missing face is never an automatic rejection — only a soft flag, still eligible.
def test_gate_keeps_faceless_photo(config: dict) -> None:
    photo = _measured(face_detected=False)
    apply_local_minimal_gate(photo, config)
    assert photo.eligible_for_openai
    assert photo.gate_status in {"openai_candidate", "low_priority_openai_candidate"}
    assert "local_flag_face_not_detected" in photo.soft_flags


# Images under 256 px on the long side are rejected; only the long side counts.
def test_gate_rejects_under_256_on_the_long_side(config: dict) -> None:
    photo = _measured(width=200, height=200)
    apply_local_minimal_gate(photo, config)
    assert photo.gate_status == "hard_reject"
    assert photo.hard_reject_reason == "too_small"
    assert photo.status == "rejected" and photo.reject_reason == "too_small"

    narrow = _measured(width=255, height=100)
    apply_local_minimal_gate(narrow, config)
    assert narrow.gate_status == "hard_reject"

    thin_strip = _measured(width=256, height=60)
    apply_local_minimal_gate(thin_strip, config)
    assert thin_strip.gate_status != "hard_reject"  # long side 256 is kept, however thin


# Near-duplicates are kept as duplicate_candidate (not rejected).
def test_gate_near_duplicate_is_candidate(config: dict) -> None:
    photo = _measured(is_near_duplicate=True)
    apply_local_minimal_gate(photo, config)
    assert photo.gate_status == "duplicate_candidate"
    assert photo.eligible_for_openai


# --- Image preparation (privacy-critical) ---------------------------------------------

# A wide photo keeps its aspect ratio (no forced square, no crop) and leaves no temp file.
def test_prepare_image_preserves_aspect_and_cleans_temp(tmp_path: Path) -> None:
    src = tmp_path / "wide.jpg"
    Image.fromarray(np.full((500, 1000, 3), 90, dtype="uint8")).save(src, "JPEG")
    prepared = prepare_image(str(src), max_side=512, jpeg_quality=80, work_dir=tmp_path)
    assert prepared.width == 512 and prepared.height == 256  # aspect preserved
    assert prepared.width != prepared.height  # never forced square
    assert prepared.b64
    assert list(temp_dir(tmp_path).glob("*")) == []  # temp copy deleted


# The prepared copy carries no EXIF even when the source had some.
def test_prepare_image_strips_exif(tmp_path: Path) -> None:
    src = tmp_path / "exif.jpg"
    image = Image.new("RGB", (800, 600), (120, 100, 90))
    exif = image.getexif()
    exif[274] = 3  # Orientation
    exif[271] = "TestCameraMake"  # Make
    image.save(src, "JPEG", exif=exif)

    prepared = prepare_image(str(src), max_side=1024, jpeg_quality=88, work_dir=tmp_path)
    out = Image.open(io.BytesIO(base64.b64decode(prepared.b64)))
    assert len(out.getexif()) == 0


# --- Result parsing -------------------------------------------------------------------

# Valid broad-scan JSON parses; the candid alias maps to second_photo.
def test_parse_broad_and_slot_alias() -> None:
    raw = '{"score": 8, "category": "CANDID", "best_slot": "candid_photo", "reject": false, "reason": "ok"}'
    result = parser.parse("broad_scan", raw)
    assert result is not None
    assert result["score"] == 8.0
    assert result["best_slot"] == "second_photo"


# Junk / non-JSON responses recover to None rather than raising.
def test_parse_invalid_json_returns_none() -> None:
    assert parser.parse("broad_scan", "the model refused to answer") is None
    assert parser.parse("quality_review", "") is None


# --- Budget guard ---------------------------------------------------------------------

# The guard allows requests up to the hard stop, then refuses and marks itself stopped.
def test_budget_guard_hard_stop(config: dict) -> None:
    guard = BudgetGuard(config, override_max=0.001)
    assert guard.reserve(0.0006) is True
    assert guard.reserve(0.0006) is False  # would exceed 0.001
    assert guard.stopped is True


# --- Cache ----------------------------------------------------------------------------

# A written cache record round-trips by key.
def test_cache_roundtrip(config: dict, tmp_path: Path) -> None:
    config["output"]["working_dir"] = str(tmp_path)
    cache.append_cache(
        config, photo_hash="h1", stage="broad_scan", model="m", prompt_version="v1",
        result_json={"score": 7.0}, estimated_cost_usd=0.001,
    )
    loaded = cache.load_cache(config)
    key = cache.cache_key("h1", "broad_scan", "m", "v1")
    assert key in loaded
    assert loaded[key]["result_json"]["score"] == 7.0


# --- Cost estimator -------------------------------------------------------------------

# Low detail is a small flat token cost; high detail costs more; request cost is positive.
def test_cost_estimator(config: dict) -> None:
    assert ce.estimate_image_tokens("low", 4000, 3000) == 85
    assert ce.estimate_image_tokens("high", 1024, 768) > 85
    cost = ce.estimate_request_cost("gpt-4.1-mini", "broad_scan", 512, 384, config)
    assert cost > 0


# --- Consensus ranking ----------------------------------------------------------------

# Consensus ordering reflects OpenAI scores and the Final Judge sets Top 6 + slot.
def test_consensus_and_final_judge(config: dict) -> None:
    photos = []
    for i in range(3):
        p = _measured(id=f"p{i}", file_path=f"{i}.jpg", file_name=f"{i}.jpg")
        p.eligible_for_openai = True
        p.gate_status = "openai_candidate"
        p.final_score = 6.0
        p.openai_broad_score = 5.0 + i
        p.openai_quality_score = 5.0 + i
        p.openai_premium_score = 5.0 + i
        p.cluster_id = -1
        photos.append(p)

    judge = {
        "top_6": [{"photo_id": "p2", "slot": "main_photo", "reason": "best", "risk": ""}],
        "top_20": ["p1"],
        "slot_candidates": {},
        "final_notes": "ok",
    }
    final_set = cr.rank_for_openai(photos, config, judge)
    by_id = {p.id: p for p in photos}
    assert by_id["p2"].consensus_score >= by_id["p0"].consensus_score
    assert by_id["p2"].in_top_6 is True
    assert by_id["p2"].final_selected_slot == "main_photo"
    assert all(p.in_top_100 for p in photos)
    assert any(p.id == "p2" for p in final_set)


# --- Orchestration with a fake client (no network) ------------------------------------

class _FakeClient:
    """Stand-in OpenAI client returning canned JSON so run_stage needs no network."""

    has_key = True

    # Always resolves to a dummy model name.
    def resolve_model(self, stage: str) -> str:
        return "fake-model"

    # Returns a fixed valid broad-scan verdict regardless of the image.
    def review_image(self, *, model, prompt, image_data_url, detail, max_output_tokens) -> str:
        return '{"score": 8, "face": 8, "naturalness": 7, "style": 7, "context": 6, "category": "HEADSHOT", "best_slot": "main_photo", "reject": false, "reason": "clear"}'


# run_stage applies parsed scores, writes the cache, and leaves no temp files.
def test_run_stage_with_fake_client(config: dict, tmp_path: Path) -> None:
    config["output"]["working_dir"] = str(tmp_path)
    src = tmp_path / "cand.jpg"
    Image.fromarray(np.full((1200, 900, 3), 130, dtype="uint8")).save(src, "JPEG")
    photo = _measured(id="c1", file_path=str(src), file_name="cand.jpg", width=900, height=1200)
    photo.eligible_for_openai = True
    photo.gate_status = "openai_candidate"
    photo.openai_priority = 1
    photo.file_hash = "hashc1"

    guard = BudgetGuard(config)
    summary = run_stage("broad_scan", [photo], config, budget=guard, client=_FakeClient())

    assert summary["processed"] == 1
    assert photo.openai_broad is not None
    assert photo.openai_broad_score == 8.0
    assert list(temp_dir(tmp_path).glob("*")) == []  # temp copies cleaned up
    assert cache.cache_path(config).exists()  # result cached
