"""Tests for order-independent grouping. Vectors are synthetic; no images are read."""

import random

import numpy as np

from src import db
from src.embeddings import assign_clusters
from src.utils import Photo, apply_data_root


def _unit(values: list[float]) -> np.ndarray:
    vec = np.asarray(values, dtype=np.float32)
    return vec / np.linalg.norm(vec)


# Builds photos and stores their vectors; spec maps id -> (vector, EXIF time or None).
def _setup(config: dict, tmp_path, spec: dict) -> tuple[dict, object, list[Photo]]:
    cfg = apply_data_root(dict(config), str(tmp_path / "root"))
    conn = db.connect(cfg)
    photos = []
    for pid, (vec, taken) in spec.items():
        photo = Photo(id=pid, file_path=f"{pid}.jpg", file_name=f"{pid}.jpg", file_hash=pid)
        if taken:
            photo.exif = {"DateTimeOriginal": taken}
        db.upsert_photo(conn, photo)
        db.save_embedding(conn, pid, vec, "test")
        photos.append(photo)
    return cfg, conn, photos


def _partition(photos: list[Photo]) -> set[frozenset[str]]:
    groups: dict[int, set[str]] = {}
    for p in photos:
        groups.setdefault(p.cluster_id, set()).add(p.id)
    return {frozenset(g) for g in groups.values()}


# A chain of look-alikes plus separate subjects must give one partition for every shuffle.
def test_grouping_is_order_independent(config: dict, tmp_path) -> None:
    rng = np.random.default_rng(3)
    spec = {}
    for k in range(6):
        base = rng.normal(size=16)
        for m in range(4):
            spec[f"p{k}_{m}"] = (_unit(list(base + rng.normal(scale=0.08, size=16))), f"2024:05:01 10:0{k}:0{m}")
    # A chain: a-b and b-c are close, a-c is not.
    spec["ca"] = (_unit([1, 0, 0, 0]), None)
    spec["cb"] = (_unit([1, 0.3, 0, 0]), None)
    spec["cc"] = (_unit([1, 0.6, 0, 0]), None)
    cfg, conn, photos = _setup(config, tmp_path, spec)
    expected, count, ids = None, None, None
    for seed in range(8):
        shuffled = list(photos)
        random.Random(seed).shuffle(shuffled)
        n = assign_clusters(shuffled, cfg, conn)
        result = _partition(photos)
        labels = {p.id: p.cluster_id for p in photos}
        if expected is None:
            expected, count, ids = result, n, labels
        assert result == expected and n == count and labels == ids
    assert count is not None and count < len(photos)
    conn.close()


# Close embeddings far apart in time split; a missing or invalid time still groups by embedding.
def test_time_gate_and_missing_times(config: dict, tmp_path) -> None:
    v = _unit([1, 0, 0])
    spec = {
        "a": (v, "2024:05:01 10:00:00"),
        "b": (v, "2024:05:01 10:00:30"),
        "c": (v, "2024:05:03 10:00:00"),
        "d": (v, None),
        "e": (v, "not a date"),
    }
    cfg, conn, photos = _setup(config, tmp_path, spec)
    assign_clusters(photos, cfg, conn)
    by_id = {p.id: p.cluster_id for p in photos}
    assert by_id["a"] == by_id["b"]
    assert by_id["a"] != by_id["c"]
    assert by_id["d"] == by_id["e"]
    conn.close()


# Vectors of different sizes never share a group, and photos without a vector stay at -1.
def test_different_sizes_never_share_a_group(config: dict, tmp_path) -> None:
    spec = {
        "a": (_unit([1, 0, 0]), None),
        "b": (_unit([1, 0, 0, 0]), None),
    }
    cfg, conn, photos = _setup(config, tmp_path, spec)
    photos.append(Photo(id="z", file_path="z.jpg", file_name="z.jpg", file_hash="z"))
    assert assign_clusters(photos, cfg, conn) == 2
    by_id = {p.id: p.cluster_id for p in photos}
    assert by_id["a"] != by_id["b"] and by_id["z"] == -1
    conn.close()
