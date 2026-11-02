"""Tests for resumable analysis: per-photo, per-stage progress in SQLite, kills mid-stage, pause, resume and cancel.

The pipeline runs for real on six synthetic images under pytest's tmp_path. The GPU models are fakes, and a fake
landmarker finds no face, so no model file, GPU or network is needed. A kill is simulated by raising an exception
that the engine does not catch (a BaseException), so nothing after that point runs. The API test drives the real
engine through the run routes. No personal photo is read.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

import main as engine_main
from api.app import create_app
from api.security import TOKEN_ENV_VAR
from api.storage import ApiSettings, library_config, library_control_file
from src import content_tags, db, face_quality, ingest, neural_stages
from src.content_tags import CONTENT_TAGS
from src.gpu_models import QUALITY
from src.model_loader import ModelUnavailable
from src.utils import Photo, apply_data_root, control_file_path, load_config, working_copy_path

ENGINE_DIR = Path(__file__).resolve().parents[1]
PHOTO_COUNT = 6
BATCH = 2
DIM = len(CONTENT_TAGS)
PER_PHOTO_STAGES = ("ingest", "quality", "faces", "qrealign", "embeddings", "content_tags", "aesthetic")


class Killed(BaseException):
    """Stands in for a killed process: the engine catches Exception only, so this ends the run on the spot."""


class Work:
    """Records the photos each stage really worked on, and kills the run when a chosen unit of work starts.

    `kill` is (stage, n): the n-th unit of that stage (a photo, a GPU batch or a whole stage call) raises Killed
    before it does anything, so the unit in flight is never recorded as finished.
    """

    def __init__(self, kill: tuple[str, int] | None = None) -> None:
        self.kill = kill
        self.started: dict[str, int] = defaultdict(int)
        self.finished: dict[str, list[str]] = defaultdict(list)
        self.chunk: list[str] = []
        self.calls: dict[str, int] = defaultdict(int)

    # Counts the start of one unit of `stage`, and raises Killed when it is the chosen one.
    def start(self, stage: str) -> None:
        self.started[stage] += 1
        if self.kill == (stage, self.started[stage]):
            raise Killed(stage)

    # Records the photos one unit of `stage` finished.
    def finish(self, stage: str, photo_ids: Sequence[str]) -> None:
        self.finished[stage].extend(photo_ids)


class FakeScorer:
    """Q-ReAlign stand-in: quality is the mean brightness and aesthetics its complement; one unit per batch."""

    name = "fake-qrealign"
    device = "cpu"

    def __init__(self, work: Work) -> None:
        self.work = work

    def score(self, images: Sequence[Image.Image], task: str) -> list[float]:
        if task == QUALITY:
            self.work.start("qrealign")
            self.work.finish("qrealign", self.work.chunk)
        means = [float(np.asarray(image, dtype=np.float32).mean()) / 255.0 for image in images]
        return means if task == QUALITY else [1.0 - m for m in means]


class FakeEmbedder:
    """SigLIP 2 stand-in: a deterministic positive vector from a 4x4 reduction of the image; one unit per batch."""

    name = "fake-siglip2"
    device = "cpu"

    def __init__(self, work: Work) -> None:
        self.work = work

    def embed(self, images: Sequence[Image.Image]) -> np.ndarray:
        self.work.start("embeddings")
        self.work.finish("embeddings", self.work.chunk)
        rows = [np.asarray(image.resize((4, 4)), dtype=np.float32).ravel()[:DIM] + 1.0 for image in images]
        return np.stack(rows)


class FakeTextEncoder:
    """Text tower stand-in: label i is the i-th axis, so each photo gets the label of its largest component."""

    name = "fake-siglip2"
    device = "cpu"

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        return np.eye(len(texts), DIM, dtype=np.float32) * 3.0


class NoFaces:
    """Face Landmarker stand-in that finds no face, so the people penalty is 0."""

    def detect(self, rgb: np.ndarray) -> list[Any]:
        return []


# Writes a blocky random-texture JPEG of 320x400 that passes the sanity gate, distinct for each seed.
def _photo(path: Path, seed: int) -> Path:
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 256, size=(40, 32, 3)).astype("uint8")
    Image.fromarray(blocks).resize((320, 400), Image.Resampling.NEAREST).save(path, "JPEG", quality=90)
    return path


@pytest.fixture()
def library(tmp_path: Path) -> dict[str, Any]:
    """Six synthetic photos and an engine config file with GPU batches of two."""
    photos = tmp_path / "photos"
    photos.mkdir()
    for seed in range(PHOTO_COUNT):
        _photo(photos / f"p{seed}.jpg", seed)
    config = load_config()
    config["models"]["qrealign"]["batch_size"] = BATCH
    config["models"]["siglip2"]["batch_size"] = BATCH
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return {"photos": photos, "config": config_path, "tmp": tmp_path}


# Replaces the stages' models and per-photo functions with recording ones that report to `work`.
def _instrument(monkeypatch: pytest.MonkeyPatch, work: Work) -> None:
    real_save = ingest._save_jpeg

    def save(image: Image.Image, target: Path, quality: int, **options: Any) -> None:
        if target.parent.name != "copies":  # only the working copy counts as the decode of an original
            real_save(image, target, quality, **options)
            return
        work.start("ingest")
        real_save(image, target, quality, **options)
        work.finish("ingest", [target.stem])

    real_open = neural_stages._open_images

    def open_images(chunk: Sequence[Photo], config: dict[str, Any]) -> Any:
        work.chunk = [photo.id for photo in chunk]
        return real_open(chunk, config)

    def unit(stage: str, func: Callable[..., Any]) -> Callable[..., Any]:
        def run(photo: Photo, config: dict[str, Any]) -> Any:
            work.start(stage)
            result = func(photo, config)
            work.finish(stage, [photo.id])
            return result

        return run

    def tags(photos: list[Photo], config: dict[str, Any], conn: Any, **kwargs: Any) -> Any:
        work.start("content_tags")
        report = content_tags.tag_content(photos, config, conn, encoder=FakeTextEncoder(), **kwargs)
        work.finish("content_tags", [p.id for p in photos if p.status != "rejected"])
        return report

    def aesthetic(photos: list[Photo], config: dict[str, Any], conn: Any) -> None:
        work.start("aesthetic")
        real_aesthetic(photos, config, conn)
        work.finish("aesthetic", [p.id for p in photos if p.status != "rejected"])

    def clusters(photos: list[Photo], config: dict[str, Any], conn: Any) -> int:
        work.calls["grouping"] += 1
        return real_clusters(photos, config, conn)

    real_aesthetic, real_clusters = engine_main.score_aesthetic, engine_main.assign_clusters
    monkeypatch.setattr(ingest, "_save_jpeg", save)
    monkeypatch.setattr(neural_stages, "_open_images", open_images)
    monkeypatch.setattr(engine_main, "score_quality", unit("quality", engine_main.score_quality))
    monkeypatch.setattr(
        engine_main, "score_faces",
        unit("faces", lambda photo, config: face_quality.score_faces(photo, config, landmarker=NoFaces())),
    )  # fmt: skip
    monkeypatch.setattr(
        engine_main, "score_qrealign",
        lambda photos, config, conn, **kw: neural_stages.score_qrealign(photos, config, conn, scorer=FakeScorer(work), **kw),
    )  # fmt: skip
    monkeypatch.setattr(
        engine_main, "embed_siglip2",
        lambda photos, config, conn, **kw: neural_stages.embed_siglip2(photos, config, conn, embedder=FakeEmbedder(work), **kw),
    )  # fmt: skip
    monkeypatch.setattr(engine_main, "tag_content", tags)
    monkeypatch.setattr(engine_main, "score_aesthetic", aesthetic)
    monkeypatch.setattr(engine_main, "assign_clusters", clusters)


# Returns the parsed arguments of `analyze` over the library photos, with all generated data under `data_root`.
def _args(library: dict[str, Any], data_root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        input=str(library["photos"]), no_subfolders=False, gemma=False, config=str(library["config"]),
        data_root=str(data_root), exports_root=None, preset=None,
    )  # fmt: skip


# Runs `analyze` in this process with fresh instruments and returns its exit code and what each stage worked on.
def _analyze(
    monkeypatch: pytest.MonkeyPatch, library: dict[str, Any], data_root: Path, kill: tuple[str, int] | None = None
) -> tuple[int | None, Work]:
    work = Work(kill)
    with monkeypatch.context() as patch:
        _instrument(patch, work)
        try:
            code: int | None = engine_main.cmd_analyze(_args(library, data_root))
        except Killed:
            code = None
    return code, work


# Returns the engine config of a data root, as `analyze --data-root` builds it.
def _config(library: dict[str, Any], data_root: Path) -> dict[str, Any]:
    return apply_data_root(load_config(str(library["config"])), str(data_root))


# Reads everything the run stored that the results depend on, keyed by photo ID, for comparison across runs.
def _results(library: dict[str, Any], data_root: Path) -> dict[str, Any]:
    with closing(db.connect(_config(library, data_root))) as conn:
        photos = {
            p.id: (p.final_score, p.technical_score, p.face_quality_score, p.aesthetic_score, p.people_penalty,
                   p.cluster_id, p.gate_status, tuple(p.soft_flags), p.status, p.is_near_duplicate)
            for p in db.load_photos(conn)
        }  # fmt: skip
        vectors = {key: value.tolist() for key, value in db.load_embeddings(conn).items()}
        return {
            "photos": photos,
            "neural": db.load_neural_scores(conn),
            "tags": db.load_content_tags(conn),
            "vectors": vectors,
        }


# Returns the IDs recorded as done for `stage` in a data root.
def _done(library: dict[str, Any], data_root: Path, stage: str) -> set[str]:
    with closing(db.connect(_config(library, data_root))) as conn:
        return db.done_ids(conn, stage)


# Returns the content-hash IDs of the library photos.
def _ids(library: dict[str, Any]) -> list[str]:
    return sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in library["photos"].iterdir())


# --- a kill in mid-stage --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kill",
    [("ingest", 3), ("quality", 3), ("faces", 4), ("qrealign", 2), ("embeddings", 3), ("content_tags", 1),
     ("aesthetic", 1)],
)  # fmt: skip
def test_a_rerun_after_a_kill_in_mid_stage_repeats_no_finished_work(
    monkeypatch: pytest.MonkeyPatch, library: dict[str, Any], kill: tuple[str, int]
) -> None:
    killed_root = library["tmp"] / "killed"
    code, first = _analyze(monkeypatch, library, killed_root, kill)
    assert code is None, "the run must have been killed"
    code, second = _analyze(monkeypatch, library, killed_root)
    assert code == 0

    ids = _ids(library)
    for stage in PER_PHOTO_STAGES:
        before, after = first.finished[stage], second.finished[stage]
        assert not set(before) & set(after), f"{stage} repeated finished photos"
        assert sorted(before + after) == ids, f"{stage} did not cover every photo exactly once"

    # The resumed library holds exactly what an uninterrupted run stores.
    code, _ = _analyze(monkeypatch, library, library["tmp"] / "reference")
    assert code == 0
    assert _results(library, killed_root) == _results(library, library["tmp"] / "reference")


def test_batch_progress_is_recorded_after_each_batch_not_at_the_end(
    monkeypatch: pytest.MonkeyPatch, library: dict[str, Any]
) -> None:
    root = library["tmp"] / "data"
    code, work = _analyze(monkeypatch, library, root, kill=("qrealign", 3))
    assert code is None
    finished = set(work.finished["qrealign"])
    assert len(finished) == 2 * BATCH
    assert _done(library, root, "qrealign") == finished
    with closing(db.connect(_config(library, root))) as conn:
        assert set(db.load_neural_scores(conn)) == finished  # every photo marked done has its values stored
    assert _done(library, root, "quality") == set(_ids(library))
    assert _done(library, root, "embeddings") == set()
    assert _done(library, root, "rank") == set()


def test_a_finished_library_reruns_without_any_work(monkeypatch: pytest.MonkeyPatch, library: dict[str, Any]) -> None:
    root = library["tmp"] / "data"
    assert _analyze(monkeypatch, library, root)[0] == 0
    stored = _results(library, root)
    code, again = _analyze(monkeypatch, library, root)
    assert code == 0
    assert {stage: again.finished[stage] for stage in PER_PHOTO_STAGES} == {stage: [] for stage in PER_PHOTO_STAGES}
    assert again.calls["grouping"] == 0  # the stored groups match the unchanged inputs
    assert _results(library, root) == stored


def test_a_photo_ingested_anew_runs_every_stage_again_alone(
    monkeypatch: pytest.MonkeyPatch, library: dict[str, Any]
) -> None:
    root = library["tmp"] / "data"
    assert _analyze(monkeypatch, library, root)[0] == 0
    lost = _ids(library)[0]
    working_copy_path(_config(library, root), lost).unlink()
    code, again = _analyze(monkeypatch, library, root)
    assert code == 0
    for stage in PER_PHOTO_STAGES:
        assert again.finished[stage] == [lost], stage
    # The working copy is rebuilt byte for byte, so its vector and the grouping inputs are unchanged.
    assert again.calls["grouping"] == 0


def test_changed_grouping_inputs_regroup(monkeypatch: pytest.MonkeyPatch, library: dict[str, Any]) -> None:
    root = library["tmp"] / "data"
    assert _analyze(monkeypatch, library, root)[0] == 0
    _photo(library["photos"] / "p_new.jpg", 99)
    code, again = _analyze(monkeypatch, library, root)
    assert code == 0
    assert again.calls["grouping"] == 1
    new = hashlib.sha256((library["photos"] / "p_new.jpg").read_bytes()).hexdigest()
    assert again.finished["embeddings"] == [new]


def test_a_missing_model_leaves_its_stage_pending_for_the_next_run(
    monkeypatch: pytest.MonkeyPatch, library: dict[str, Any]
) -> None:
    root = library["tmp"] / "data"

    def unavailable(config: dict[str, Any]) -> Any:
        raise ModelUnavailable("qrealign", "directory_missing", "not here")

    with monkeypatch.context() as patch:
        _instrument(patch, Work())
        patch.setattr(
            engine_main, "score_qrealign",
            lambda photos, config, conn, **kw: neural_stages.score_qrealign(photos, config, conn, loader=unavailable, **kw),
        )  # fmt: skip
        assert engine_main.cmd_analyze(_args(library, root)) == 0
    assert _done(library, root, "qrealign") == set()
    code, later = _analyze(monkeypatch, library, root)
    assert code == 0
    assert sorted(later.finished["qrealign"]) == _ids(library)


# --- cancel, pause and resume ---------------------------------------------------------------------------------------


def test_a_cancel_in_mid_stage_keeps_the_finished_photos_and_resumes(
    monkeypatch: pytest.MonkeyPatch, library: dict[str, Any]
) -> None:
    root = library["tmp"] / "data"
    work = Work()

    def cancel_after_two(photo: Photo, config: dict[str, Any]) -> Photo:
        if len(work.finished["quality"]) == 1:  # the second photo asks for the cancel
            control_file_path(config).write_text(json.dumps({"paused": False, "cancel": True}), encoding="utf-8")
        return photo

    with monkeypatch.context() as patch:
        patch.setattr(engine_main, "score_quality", cancel_after_two)
        _instrument(patch, work)
        assert engine_main.cmd_analyze(_args(library, root)) == 2
    assert len(work.finished["quality"]) == 2
    assert _done(library, root, "quality") == set(work.finished["quality"])
    assert _done(library, root, "faces") == set()

    control_file_path(_config(library, root)).write_text(json.dumps({"paused": False, "cancel": False}), "utf-8")
    code, resumed = _analyze(monkeypatch, library, root)
    assert code == 0
    assert not set(resumed.finished["quality"]) & set(work.finished["quality"])
    assert len(resumed.finished["quality"]) == PHOTO_COUNT - 2


def test_a_cancel_between_gpu_batches_stops_the_stage(monkeypatch: pytest.MonkeyPatch, library: dict[str, Any]) -> None:
    root = library["tmp"] / "data"
    config = _config(library, root)

    class CancellingScorer(FakeScorer):
        def score(self, images: Sequence[Image.Image], task: str) -> list[float]:
            control_file_path(config).write_text(json.dumps({"paused": False, "cancel": True}), encoding="utf-8")
            return super().score(images, task)

    work = Work()
    with monkeypatch.context() as patch:
        _instrument(patch, work)
        patch.setattr(
            engine_main, "score_qrealign",
            lambda photos, cfg, conn, **kw: neural_stages.score_qrealign(photos, cfg, conn, scorer=CancellingScorer(work), **kw),
        )  # fmt: skip
        assert engine_main.cmd_analyze(_args(library, root)) == 2
    assert len(work.finished["qrealign"]) == BATCH  # the batches after the cancel never ran
    assert _done(library, root, "qrealign") == set(work.finished["qrealign"])


# --- the same through the API, with the real engine -----------------------------------------------------------------

TOKEN = "test-session-token-0123456789"
HOSTS = ("127.0.0.1", "localhost")
LIB = "resume-library"
WAIT_SECONDS = 60.0

# Runs the real engine. While HOLD exists it holds the first scored photo until the run is paused and the third
# until it is cancelled, so the test acts at known points. Every photo the quality stage scores is logged.
HELPER = r'''
import sys
import time
from pathlib import Path

sys.path.insert(0, {engine!r})
import main
from src.utils import read_control

LOG = Path({log!r})
HOLD = Path({hold!r})
_real = main.score_quality
_calls = []


def _wait_for(config, index):
    deadline = time.monotonic() + 30
    while not read_control(config)[index] and time.monotonic() < deadline:
        time.sleep(0.05)


def held_quality(photo, config):
    result = _real(photo, config)
    _calls.append(photo.id)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(photo.id + "\n")
    if HOLD.exists() and len(_calls) == 1:
        _wait_for(config, 0)
    if HOLD.exists() and len(_calls) == 3:
        _wait_for(config, 1)
    return result


main.score_quality = held_quality
raise SystemExit(main.main(sys.argv[1:]))
'''


# Polls `predicate` until it holds, or fails the test after WAIT_SECONDS.
def _wait_until(predicate: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


# Reads the IDs done for a stage straight from the library database with a plain SELECT (db.connect would write the
# schema), so the test never competes for the engine's write lock. Returns an empty set before the table exists.
def _api_done(settings: ApiSettings, stage: str) -> set[str]:
    path = Path(library_config(settings, LIB)["output"]["db_path"])
    if not path.is_file():
        return set()
    try:
        with closing(sqlite3.connect(str(path), timeout=5)) as conn:
            rows = conn.execute("SELECT photo_id FROM jobs WHERE stage = ? AND state = 'done'", (stage,)).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {row[0] for row in rows}


# Returns the photo IDs the helper logged, in order.
def _logged(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").split() if log.is_file() else []


def test_pause_resume_and_cancel_through_the_api_keep_finished_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN)
    photos = tmp_path / "photos"
    photos.mkdir()
    for seed in range(PHOTO_COUNT):
        _photo(photos / f"p{seed}.jpg", seed)
    log, hold = tmp_path / "quality.log", tmp_path / "hold"
    hold.touch()
    helper = tmp_path / "helper" / "engine_helper.py"
    helper.parent.mkdir()
    helper.write_text(HELPER.format(engine=str(ENGINE_DIR), log=str(log), hold=str(hold)), encoding="utf-8")
    settings = ApiSettings(
        data_root=tmp_path / "data",
        photos_root=photos,
        exports_root=tmp_path / "exports",
        engine_command=(sys.executable, str(helper)),
    )
    app = create_app(TOKEN, HOSTS, settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"})
    status = f"/api/libraries/{LIB}/runs/current"
    try:
        assert client.post(f"/api/libraries/{LIB}/runs").status_code == 202
        _wait_until(lambda: len(_logged(log)) == 1, "the first scored photo")
        assert client.post(f"{status}/pause").json()["paused"] is True
        _wait_until(lambda: len(_api_done(settings, "quality")) == 1, "the first photo to be recorded")
        time.sleep(0.5)
        assert len(_logged(log)) == 1, "a paused run must not score another photo"
        assert client.get(status).json()["paused"] is True

        assert client.post(f"{status}/resume").json()["paused"] is False
        _wait_until(lambda: len(_logged(log)) == 3, "the third scored photo")
        assert len(_api_done(settings, "quality")) == 2  # the photo in flight is not recorded yet
        assert client.post(f"{status}/cancel").status_code == 200
        _wait_until(lambda: not client.get(status).json()["running"], "the cancelled run to stop")
        assert client.get(status).json()["message"] == "Analysis cancelled by user."
        client.get(f"/api/libraries/{LIB}/events")  # the stream ends only once the engine process has exited
        first = _logged(log)
        assert _api_done(settings, "quality") == set(first)
        assert _api_done(settings, "faces") == set()
        copies = Path(library_config(settings, LIB)["output"]["working_dir"]) / "copies"
        stamps = {p.name: p.stat().st_mtime_ns for p in copies.iterdir()}
        assert len(stamps) == PHOTO_COUNT

        hold.unlink()
        assert client.post(f"/api/libraries/{LIB}/runs").status_code == 202
        _wait_until(lambda: not client.get(status).json()["running"], "the resumed run to finish")
        assert client.get(status).json()["step"] == "done"
        second = _logged(log)[len(first):]
        assert not set(first) & set(second), "the resumed run scored a finished photo again"
        assert len(set(first + second)) == PHOTO_COUNT
        assert {p.name: p.stat().st_mtime_ns for p in copies.iterdir()} == stamps  # nothing decoded again
        assert json.loads(library_control_file(settings, LIB).read_text(encoding="utf-8"))["cancel"] is False
    finally:
        app.state.runs.shutdown()


# --- storage ---------------------------------------------------------------------------------------------------------


def test_updating_a_photo_keeps_its_model_results(data_config: dict) -> None:
    conn = db.connect(data_config)
    photo = Photo(id="a" * 64, file_path="a.jpg", file_name="a.jpg")
    db.upsert_photo(conn, photo)
    db.save_neural_scores(conn, photo.id, "fake", 0.4, 0.6)
    db.save_embedding(conn, photo.id, np.ones(DIM, dtype=np.float32), "fake")
    db.save_content_tag(conn, photo.id, "landscape", 0.3, "fake")
    photo.technical_score = 70.0
    db.upsert_photo(conn, photo)
    assert db.load_neural_scores(conn)[photo.id]["quality"] == 0.4
    assert photo.id in db.load_embeddings(conn)
    assert db.load_content_tags(conn)[photo.id]["tag"] == "landscape"
    conn.close()


def test_a_stored_photo_loads_back_with_its_exact_values(data_config: dict) -> None:
    conn = db.connect(data_config)
    photo = Photo(
        id="b" * 64, file_path="b.jpg", file_name="b.jpg", file_hash="b" * 64, perceptual_hash="ff00ff00ff00ff00",
        exif={"DateTimeOriginal": "2024:05:01 10:00:00"}, technical_score=71.23456, people_penalty=0.0123456,
        brightness_mean=33.3333, laplacian_variance=12.5, resolution_score=88.8, face_clarity_score=41.987,
    )  # fmt: skip
    db.upsert_photo(conn, photo)
    loaded = db.load_photos(conn)[0]
    conn.close()
    for name in ("file_hash", "perceptual_hash", "exif", "technical_score", "people_penalty", "brightness_mean",
                 "laplacian_variance", "resolution_score", "face_clarity_score"):  # fmt: skip
        assert getattr(loaded, name) == getattr(photo, name), name


def test_set_stages_and_done_ids_track_each_photo_and_stage(data_config: dict) -> None:
    conn = db.connect(data_config)
    db.set_stages(conn, ["p1", "p2"], "qrealign", "done")
    db.set_stage(conn, "p3", "qrealign", "error")
    assert db.done_ids(conn, "qrealign") == {"p1", "p2"}
    assert db.done_ids(conn, "embeddings") == set()
    db.clear_stages(conn, "p1")
    assert db.done_ids(conn, "qrealign") == {"p2"}
    db.save_stage_run(conn, "grouping", "key-1", {"p2": 0})
    assert db.load_stage_run(conn, "grouping", "key-1") == {"p2": 0}
    assert db.load_stage_run(conn, "grouping", "key-2") is None
    conn.close()
