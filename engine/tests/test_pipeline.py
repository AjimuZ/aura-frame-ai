"""Tests for the SQLite persistence, ingest, dedup, embeddings, and VLM runner.

All images are SYNTHETIC. No personal photos are read. Heavy ML (torch/open_clip) is not
required: embeddings and aesthetic fall back gracefully and these tests assert that path.
"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import db
from src.embeddings import assign_clusters, embed_photos
from src.ingest import ingest_image
from src.index_photos import index_photos, mark_duplicates
from src.local_gate import apply_local_minimal_gate
from src.quality_score import score_quality
from src.utils import Photo, file_sha256


# Saves a numpy RGB array as a JPEG and returns the path.
def _save_jpg(array: np.ndarray, path: Path) -> Path:
    Image.fromarray(array.astype("uint8")).save(path, "JPEG", quality=90)
    return path


@pytest.fixture()
def textured(tmp_path: Path) -> Path:
    cols = np.indices((900, 1200))[1]
    base = ((cols // 7) % 2) * 120 + 60
    rgb = np.stack([base, base * 0.7, base * 0.5], axis=-1)
    return _save_jpg(rgb, tmp_path / "a.jpg")


# A redirected data root keeps every generated artifact under the temp dir.
@pytest.fixture()
def db_config(config: dict, tmp_path: Path) -> dict:
    from src.utils import apply_data_root

    return apply_data_root(dict(config), str(tmp_path / "dataroot"))


# Ingest must compute a stable file hash, a perceptual hash, and a thumbnail.
def test_ingest_populates_metadata(textured: Path, db_config: dict) -> None:
    photo = ingest_image(textured, db_config)
    assert photo.file_hash == file_sha256(textured)
    assert photo.perceptual_hash
    assert photo.width > 0 and photo.height > 0
    assert Path(photo.thumbnail_path).exists()


# EXIF orientation must be normalized so reported dimensions match the visual image.
def test_exif_orientation_normalized(tmp_path: Path, db_config: dict) -> None:
    rgb = np.zeros((400, 800, 3), dtype="uint8")
    rgb[:, :100] = 255  # asymmetric so a rotation would change reported size
    path = tmp_path / "rot.jpg"
    img = Image.fromarray(rgb)
    exif = img.getexif()
    exif[274] = 6  # Orientation = rotate 90 CW
    img.save(path, "JPEG", exif=exif)
    photo = ingest_image(path, db_config)
    # After exif_transpose the 800x400 landscape becomes 400x800 portrait.
    assert (photo.width, photo.height) == (400, 800)


# The database round-trips a photo and tracks per-stage state for resumability.
def test_db_roundtrip_and_stages(db_config: dict) -> None:
    conn = db.connect(db_config)
    photo = Photo(id="IMG_X", file_path="x.jpg", file_name="x.jpg", file_hash="abc",
                  technical_score=80, final_score=7.5, status="accepted")
    db.upsert_photo(conn, photo)
    db.set_stage(conn, "IMG_X", "rank", "done")
    assert db.get_stage(conn, "IMG_X", "rank") == "done"
    assert db.get_stage(conn, "IMG_X", "vlm") == "pending"
    loaded = {p.id: p for p in db.load_photos(conn)}
    assert "IMG_X" in loaded and loaded["IMG_X"].final_score == 7.5
    db.write_mirror(conn, db_config)
    conn.close()


# Review sessions are isolated by the selected database and the latest one is restored.
def test_latest_review_session(db_config: dict) -> None:
    conn = db.connect(db_config)
    assert db.load_latest_review_session(conn) is None
    db.save_review_session(conn, {"main_photo": "A"}, {"main_photo": True}, ["A"])
    db.save_review_session(conn, {"main_photo": "B"}, {"main_photo": True}, ["B"])
    session = db.load_latest_review_session(conn)
    conn.close()
    assert session == {
        "slotAssignments": {"main_photo": "B"},
        "locks": {"main_photo": True},
        "shortlist": ["B"],
    }


# An extreme aspect ratio is no longer auto-rejected: the minimal gate keeps it and adds an
# odd-composition soft flag (it is tall enough to clear the hard low-resolution floor).
def test_minimal_gate_flags_extreme_aspect(tmp_path: Path, config: dict) -> None:
    cols = np.indices((500, 3000))[1]
    base = ((cols // 6) % 2) * 120 + 60
    rgb = np.stack([base, base, base], axis=-1)
    path = _save_jpg(rgb, tmp_path / "pano.jpg")
    photo = Photo(id="t", file_path=str(path), file_name="pano.jpg", width=3000, height=500)
    score_quality(photo, config)
    assert photo.status != "rejected"  # measurement never rejects
    apply_local_minimal_gate(photo, config)
    assert photo.gate_status != "hard_reject"
    assert photo.eligible_for_openai
    assert "local_flag_odd_composition" in photo.soft_flags


# Embeddings fall back to the lightweight descriptor and clustering groups near-identical photos.
def test_embeddings_fallback_and_clusters(textured: Path, tmp_path: Path, db_config: dict) -> None:
    copy_path = tmp_path / "a_copy.jpg"
    copy_path.write_bytes(textured.read_bytes())
    folder = textured.parent
    conn = db.connect(db_config)
    photos = index_photos(folder, db_config, include_subfolders=False)
    for p in photos:
        db.upsert_photo(conn, p)
    embed_photos(photos, db_config, conn)
    vectors = db.load_embeddings(conn)
    assert vectors and all(v.size > 0 for v in vectors.values())
    assert all(v.flags.writeable for v in vectors.values())
    clusters = assign_clusters(photos, db_config, conn)
    assert clusters >= 1
    conn.close()


# When the local vision service is unreachable, the runner reports it without raising and the
# candidate stage leaves the candidates unchanged.
def test_vlm_runner_unavailable(config: dict) -> None:
    from src.vlm import VlmRunner, judge_candidates

    cfg = dict(config)
    cfg["ai"] = {"local": {"endpoint": "http://127.0.0.1:1", "timeout_seconds": 1}}  # unreachable
    runner = VlmRunner(cfg)
    try:
        health = runner.health()
    finally:
        runner.close()
    assert health.reachable is False
    assert health.ready is False
    photo = Photo(id="t", file_path="x.jpg", file_name="x.jpg")
    judge_candidates([photo], cfg)
    assert photo.gemma is None


# A malformed endpoint is reported as a message, never raised, so a bad setting cannot stop a run.
def test_vlm_runner_reports_an_invalid_endpoint(config: dict) -> None:
    from src.vlm import VlmRunner

    cfg = dict(config)
    cfg["ai"] = {"local": {"endpoint": "vision:8080"}}
    runner = VlmRunner(cfg)
    health = runner.health()
    runner.close()
    assert health.ready is False
    assert "http" in health.message
