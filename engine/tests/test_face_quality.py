"""Tests of the people signals: Face Landmarker readings reduced to scalars, the people penalty, and a missing model.

The landmarker is always a fake and every image is synthetic under pytest's tmp_path: no model file is
downloaded, no network is used and no personal photo is read.
"""

import copy
import dataclasses
import json
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from src import db, face_quality
from src.face_quality import FaceReading, people_penalty, reading_from_landmarks, score_faces
from src.ingest import ingest_image
from src.model_fetch.fetch import ZIP_MAGIC
from src.model_loader import ModelUnavailable, locate_task_file
from src.utils import Photo, working_copy_path

FACE_BOX = (0.30, 0.20, 0.70, 0.80)
SMALL_BOX = (0.05, 0.05, 0.15, 0.20)


# Returns a reading with the given blink values and box, and a frontal pose.
def _reading(left: float | None = 0.05, right: float | None = 0.05, box: tuple = FACE_BOX) -> FaceReading:
    return FaceReading(box=box, yaw=0.0, pitch=0.0, blink_left=left, blink_right=right)


class FakeLandmarker:
    """Returns the given readings for every image and counts the calls."""

    def __init__(self, readings: list[FaceReading]) -> None:
        self.readings = readings
        self.calls = 0

    def detect(self, rgb: np.ndarray) -> list[FaceReading]:
        self.calls += 1
        return list(self.readings)


# Ingests a synthetic photo: random noise is sharp everywhere, a flat grey image has no local sharpness.
def _photo(tmp_path: Path, config: dict, sharp: bool = True, name: str = "p.jpg") -> Photo:
    rng = np.random.default_rng(3)
    pixels = (
        rng.integers(0, 256, size=(600, 800, 3), dtype=np.uint8)
        if sharp
        else np.full((600, 800, 3), 128, dtype=np.uint8)
    )
    path = tmp_path / name
    Image.fromarray(pixels).save(path, "JPEG", quality=95)
    return ingest_image(path, config)


# Every test starts and ends without a shared landmarker, so none leaks between tests.
@pytest.fixture(autouse=True)
def _fresh_shared_landmarker() -> Any:
    face_quality.reset_landmarker()
    yield
    face_quality.reset_landmarker()


# --- penalty -------------------------------------------------------------------------------


def test_a_photo_with_no_face_has_a_penalty_of_zero(tmp_path: Path, data_config: dict) -> None:
    photo = _photo(tmp_path, data_config, sharp=False)
    photo.people_penalty = 0.5  # a stale value from an earlier run must not survive

    score_faces(photo, data_config, FakeLandmarker([]))

    assert photo.people_penalty == 0.0
    assert (photo.number_of_faces, photo.face_detected, photo.group_photo) == (0, False, False)
    assert people_penalty([], None, data_config) == 0.0


def test_open_eyes_on_a_sharp_face_have_no_penalty(tmp_path: Path, data_config: dict) -> None:
    photo = _photo(tmp_path, data_config)

    score_faces(photo, data_config, FakeLandmarker([_reading(0.05, 0.10)]))

    assert photo.face_detected and photo.number_of_faces == 1
    assert photo.face_clarity_score == 100.0
    assert photo.people_penalty == 0.0
    assert photo.eyes_visible_score == pytest.approx(92.5)
    assert 0.0 < photo.face_quality_score <= 100.0


def test_closed_eyes_give_a_small_bounded_penalty(tmp_path: Path, data_config: dict) -> None:
    photo = _photo(tmp_path, data_config)
    cap = data_config["face"]["max_penalty"]

    score_faces(photo, data_config, FakeLandmarker([_reading(0.95, 0.90)]))

    assert photo.people_penalty == pytest.approx(0.6 * cap)
    assert photo.eyes_visible_score == pytest.approx(7.5)
    assert 0.0 < photo.people_penalty <= cap < 0.25


def test_a_blurred_main_face_is_penalised_less_than_closed_eyes(tmp_path: Path, data_config: dict) -> None:
    photo = _photo(tmp_path, data_config, sharp=False)
    cap = data_config["face"]["max_penalty"]

    score_faces(photo, data_config, FakeLandmarker([_reading()]))

    assert photo.face_clarity_score == pytest.approx(0.0, abs=0.5)
    assert photo.people_penalty == pytest.approx(0.4 * cap, rel=0.05)


def test_closed_eyes_and_a_blurred_face_reach_the_cap_and_no_more(tmp_path: Path, data_config: dict) -> None:
    photo = _photo(tmp_path, data_config, sharp=False)
    cap = data_config["face"]["max_penalty"]
    score_faces(photo, data_config, FakeLandmarker([_reading(1.0, 1.0)]))
    assert photo.people_penalty == pytest.approx(cap, rel=0.05)
    assert photo.people_penalty <= cap

    # A setting above the hard cap is clipped to it.
    loose = copy.deepcopy(data_config)
    loose["face"]["max_penalty"] = 5.0
    assert people_penalty([_reading(1.0, 1.0)], 0.0, loose) == pytest.approx(face_quality.PENALTY_CAP)


def test_the_penalty_grows_with_the_blink_value(data_config: dict) -> None:
    face = data_config["face"]
    middle = (face["blink_start"] + face["blink_full"]) / 2.0
    values = [people_penalty([_reading(b, b)], 100.0, data_config) for b in (0.2, face["blink_start"], middle, 0.99)]

    assert values[0] == values[1] == 0.0
    assert values[2] == pytest.approx(0.5 * 0.6 * face["max_penalty"])
    assert values[1] < values[2] < values[3]


def test_in_a_group_the_worst_eyes_count_and_the_largest_face_is_the_subject(
    tmp_path: Path, data_config: dict
) -> None:
    photo = _photo(tmp_path, data_config)
    readings = [_reading(0.0, 0.0, FACE_BOX), _reading(1.0, 1.0, SMALL_BOX)]

    score_faces(photo, data_config, FakeLandmarker(readings))

    assert photo.number_of_faces == 2 and photo.group_photo
    assert photo.people_penalty == pytest.approx(0.6 * data_config["face"]["max_penalty"])
    assert photo.eyes_visible_score == 100.0  # the largest face has open eyes
    assert photo.face_size_score == 100.0  # a 24 % box is far above the size at which the score saturates


def test_unknown_blink_values_add_no_penalty_and_no_eye_score(tmp_path: Path, data_config: dict) -> None:
    photo = _photo(tmp_path, data_config)

    score_faces(photo, data_config, FakeLandmarker([_reading(None, 0.9)]))

    assert photo.people_penalty == 0.0
    assert photo.eyes_visible_score == 0.0
    assert photo.face_quality_score > 0.0


def test_a_failing_landmarker_leaves_the_photo_unchanged(tmp_path: Path, data_config: dict, capsys: Any) -> None:
    class Broken:
        def detect(self, rgb: np.ndarray) -> list[FaceReading]:
            raise RuntimeError("inference failed")

    photo = _photo(tmp_path, data_config)

    score_faces(photo, data_config, Broken())

    assert (photo.number_of_faces, photo.people_penalty, photo.face_detected) == (0, 0.0, False)
    assert "stay unknown" in capsys.readouterr().out


# --- reduction of the mesh ------------------------------------------------------------------


# Returns a 478-point synthetic mesh spread over 0.30-0.70 in both axes.
def _mesh() -> list[tuple[float, float]]:
    return [(0.30 + 0.40 * (i / 477), 0.30 + 0.40 * (((i * 7) % 478) / 477)) for i in range(478)]


def test_the_mesh_is_reduced_to_a_box_a_pose_and_blink_scalars() -> None:
    reading = reading_from_landmarks(_mesh(), {"eyeBlinkLeft": 1.4, "eyeBlinkRight": -0.2})

    assert reading is not None
    assert reading.box == pytest.approx((0.30, 0.30, 0.70, 0.70))
    assert (reading.blink_left, reading.blink_right) == (1.0, 0.0)
    assert -90.0 <= reading.yaw <= 90.0 and -90.0 <= reading.pitch <= 90.0
    assert all(isinstance(value, float) for value in (reading.yaw, reading.pitch, *reading.box))


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "0.5", True])
def test_a_missing_or_unusable_blink_value_is_unknown(bad: Any) -> None:
    reading = reading_from_landmarks(_mesh(), {"eyeBlinkLeft": bad})
    assert reading is not None and reading.blink_left is None and reading.blink_right is None


def test_a_mesh_too_short_for_the_points_used_gives_no_reading() -> None:
    assert reading_from_landmarks(_mesh()[:100], {}) is None


# --- no face vector -------------------------------------------------------------------------


def test_no_landmark_or_face_vector_is_computed_kept_or_stored(tmp_path: Path, data_config: dict) -> None:
    mesh = _mesh()
    sentinel = repr(mesh[200][0])

    class MeshLandmarker:
        def detect(self, rgb: np.ndarray) -> list[FaceReading]:
            reading = reading_from_landmarks(mesh, {"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.9})
            return [reading] if reading else []

    photo = _photo(tmp_path, data_config)
    score_faces(photo, data_config, MeshLandmarker())
    conn = db.connect(data_config)
    db.upsert_photo(conn, photo)
    conn.commit()

    dump = json.dumps(photo.to_camel())
    for table in [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
        dump += str([tuple(row) for row in conn.execute(f"SELECT * FROM {table}")])  # noqa: S608 - names from sqlite
    assert sentinel not in dump
    assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0
    assert "BLOB" not in {row["type"].upper() for row in conn.execute("PRAGMA table_info(faces)")}
    conn.close()

    # The result is scalars: a photo field never holds a list or an array of numbers.
    for model in (FaceReading, Photo):
        assert all("ndarray" not in str(f.type) and "landmark" not in f.name for f in dataclasses.fields(model))
    assert all("list" not in str(f.type) for f in dataclasses.fields(FaceReading))
    assert isinstance(photo.to_camel()["peoplePenalty"], float)


def test_the_penalty_survives_a_database_round_trip(tmp_path: Path, data_config: dict) -> None:
    photo = _photo(tmp_path, data_config)
    score_faces(photo, data_config, FakeLandmarker([_reading(1.0, 1.0)]))
    conn = db.connect(data_config)
    db.upsert_photo(conn, photo)
    conn.commit()

    loaded = db.load_photos(conn)[0]
    conn.close()
    assert loaded.people_penalty == pytest.approx(round(photo.people_penalty, 3))


# --- the model file -------------------------------------------------------------------------


# Returns a config whose models volume is an empty directory under tmp_path, with no models override.
def _empty_volume(config: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.delenv("AURAFRAME_MODELS_DIR", raising=False)
    changed = copy.deepcopy(config)
    changed["models"]["dir"] = str(tmp_path / "models")
    (tmp_path / "models").mkdir()
    return changed


# Fails the test if anything tries to reach the network.
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no network access was expected")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_a_missing_model_gives_guidance_once_and_never_downloads(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config = _empty_volume(data_config, tmp_path, monkeypatch)
    _forbid_network(monkeypatch)
    first, second = _photo(tmp_path, config, name="a.jpg"), _photo(tmp_path, config, sharp=False, name="b.jpg")
    capsys.readouterr()

    score_faces(first, config)
    score_faces(second, config)

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    errors = [event for event in events if event.get("level") == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "model_file_missing" and errors[0]["model"] == "face_landmarker"
    assert "--fetch-models face-landmarker" in errors[0]["message"]
    for photo in (first, second):
        assert (photo.number_of_faces, photo.people_penalty, photo.face_detected) == (0, 0.0, False)
    assert not any(path.name.endswith(".task") for path in (tmp_path / "models").iterdir())


def test_the_model_is_loaded_once_for_all_photos(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLandmarker([_reading(1.0, 1.0)])
    loads: list[int] = []
    monkeypatch.setattr(face_quality, "load_landmarker", lambda config: loads.append(1) or fake)

    for name in ("a.jpg", "b.jpg", "c.jpg"):
        score_faces(_photo(tmp_path, data_config, name=name), data_config)

    assert loads == [1] and fake.calls == 3


def test_a_present_model_without_the_package_reports_the_missing_dependency(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config = _empty_volume(data_config, tmp_path, monkeypatch)
    (tmp_path / "models" / "face_landmarker.task").write_bytes(ZIP_MAGIC + b"\x00" * 64)
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    photo = _photo(tmp_path, config)
    capsys.readouterr()

    score_faces(photo, config)

    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert event["code"] == "model_dependency_missing"
    assert photo.people_penalty == 0.0


def test_the_model_file_must_be_one_plain_task_file_with_a_zip_header(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _empty_volume(data_config, tmp_path, monkeypatch)
    volume = tmp_path / "models"

    for bad in ("../face_landmarker.task", "sub/face_landmarker.task", "https://host/f.task", "weights.bin", "", None):
        config["models"]["face_landmarker"] = {"file": bad}
        with pytest.raises(ModelUnavailable) as raised:
            locate_task_file(config, "face_landmarker", "face-landmarker")
        assert raised.value.code == "not_configured"

    config["models"]["face_landmarker"] = {"file": "face_landmarker.task"}
    with pytest.raises(ModelUnavailable) as missing:
        locate_task_file(config, "face_landmarker", "face-landmarker")
    assert missing.value.code == "file_missing"

    (volume / "face_landmarker.task").write_bytes(b"not a zip file at all")
    with pytest.raises(ModelUnavailable) as invalid:
        locate_task_file(config, "face_landmarker", "face-landmarker")
    assert invalid.value.code == "invalid_weights"

    (volume / "face_landmarker.task").write_bytes(ZIP_MAGIC + b"\x00" * 64)
    found = locate_task_file(config, "face_landmarker", "face-landmarker")
    assert found.resolve() == (volume / "face_landmarker.task").resolve()


def test_the_shipped_configuration_names_the_pinned_task_file(config: dict) -> None:
    manifest = json.loads((Path(face_quality.__file__).parent / "model_fetch" / "manifest.json").read_text("utf-8"))
    targets = {f["target"] for m in manifest["models"] if m["id"] == face_quality.FETCH_ID for f in m["files"]}
    assert targets == {config["models"]["face_landmarker"]["file"]}


def test_the_working_copy_is_what_the_landmarker_sees(tmp_path: Path, data_config: dict) -> None:
    seen: list[tuple[int, ...]] = []

    class Spy:
        def detect(self, rgb: np.ndarray) -> list[FaceReading]:
            seen.append(rgb.shape)
            return []

    photo = _photo(tmp_path, data_config)
    working = np.asarray(Image.open(working_copy_path(data_config, photo.id)))

    score_faces(photo, data_config, Spy())

    assert seen == [working.shape]
