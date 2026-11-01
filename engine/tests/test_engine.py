"""Unit tests for the AuraFrame AI engine using synthetic images only."""

import json
from pathlib import Path

import cv2

from src.final_ranker import rank_photo, select_final_set
from src.index_photos import index_photos, mark_duplicates
from src.local_gate import apply_local_minimal_gate
from src.quality_score import blur_variance, score_quality
from src.utils import Photo, photo_id_for, read_image_bgr


# A sharp checkerboard should have far higher Laplacian variance than a flat image.
def test_blur_variance_ordering(sharp_image: Path, blurry_image: Path) -> None:
    sharp = cv2.cvtColor(cv2.imread(str(sharp_image)), cv2.COLOR_BGR2GRAY)
    flat = cv2.cvtColor(cv2.imread(str(blurry_image)), cv2.COLOR_BGR2GRAY)
    assert blur_variance(sharp) > blur_variance(flat)


# score_quality only MEASURES now; the Local Minimal Gate decides. A moderately dark photo
# is kept and soft-flagged (low_light); only a truly black frame is a hard reject.
def test_minimal_gate_dark_handling(dark_image: Path, config: dict) -> None:
    photo = Photo(id="t", file_path=str(dark_image), file_name="dark.jpg", width=1000, height=1000)
    score_quality(photo, config)
    assert photo.status != "rejected"  # measurement never rejects a readable image
    apply_local_minimal_gate(photo, config)
    if photo.brightness_mean < 10:
        assert photo.gate_status == "hard_reject"
    else:
        assert photo.gate_status != "hard_reject"
        assert photo.eligible_for_openai
        assert "local_flag_low_light" in photo.soft_flags


# Byte-identical images should be detected as exact duplicates (all but one rejected).
def test_duplicate_detection(sharp_image: Path, tmp_path: Path, config: dict) -> None:
    copy_path = tmp_path / "sharp_copy.jpg"
    copy_path.write_bytes(sharp_image.read_bytes())
    photos = index_photos(tmp_path, config, include_subfolders=False)
    duplicates = mark_duplicates(photos)
    assert duplicates >= 1
    assert any(p.status == "rejected" and p.reject_reason == "duplicate_exact" for p in photos)


# Photo ids must be stable for the same path and differ across paths.
def test_photo_id_stable(tmp_path: Path) -> None:
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    assert photo_id_for(a) == photo_id_for(a)
    assert photo_id_for(a) != photo_id_for(b)


# HEIC images must be readable and scoreable (via the Pillow fallback).
def test_heic_supported(heic_image: Path, config: dict) -> None:
    image = read_image_bgr(str(heic_image))
    assert image is not None
    assert image.shape[2] == 3
    photo = Photo(
        id="t", file_path=str(heic_image), file_name="photo.heic", width=1000, height=1000
    )
    score_quality(photo, config)
    assert photo.technical_score >= 0


# Ranking a strong photo yields a positive final score and a non-reject category.
def test_rank_photo_assigns_category(config: dict) -> None:
    photo = Photo(
        id="t",
        file_path="x.jpg",
        file_name="x.jpg",
        width=1200,
        height=1500,
        technical_score=90,
        face_detected=True,
        face_clarity_score=85,
        face_size_score=60,
        status="accepted",
    )
    rank_photo(photo, config)
    assert photo.final_score > 0
    assert photo.category != "REJECT"


# The final set should contain at most six unique, non-rejected photos.
def test_select_final_set_size(config: dict) -> None:
    photos = []
    for i in range(10):
        p = Photo(
            id=f"p{i}",
            file_path=f"{i}.jpg",
            file_name=f"{i}.jpg",
            technical_score=80 - i,
            face_detected=True,
            face_clarity_score=80,
            face_size_score=50,
            status="accepted",
        )
        rank_photo(p, config)
        photos.append(p)
    final_set = select_final_set(photos)
    assert 0 < len(final_set) <= 6
    assert len({p.id for p in final_set}) == len(final_set)


# A model review is valid only with the neutral overall_score key, and the ranker reads it.
def test_review_overall_score_drives_ranking(config: dict) -> None:
    from src.vlm.schema import REQUIRED_KEYS, RESPONSE_SCHEMA, parse_review

    assert "overall_score" in REQUIRED_KEYS
    assert "overall_score" in RESPONSE_SCHEMA["properties"]
    assert "overall_score" in RESPONSE_SCHEMA["required"]

    review = {key: 5 for key in REQUIRED_KEYS if key.endswith(("_score", "_quality", "_clarity"))}
    review.update({"category": "CANDID", "best_slot": "backup"})
    scored = []
    for overall in (2, 9):
        gemma = parse_review(json.dumps({**review, "overall_score": overall}))
        assert gemma is not None and gemma["overall_score"] == overall
        photo = Photo(
            id=f"o{overall}", file_path="x.jpg", file_name="x.jpg", technical_score=60,
            face_detected=True, face_clarity_score=60, face_size_score=40,
            status="accepted", gemma=gemma,
        )
        scored.append(rank_photo(photo, config).final_score)
    assert scored[1] > scored[0]
