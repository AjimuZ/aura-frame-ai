"""Tests for the run, event and export routes.

The engine is replaced by a stub script that prints JSONL events, so no model, GPU or real
pipeline runs. Every image is synthetic and lives under pytest's tmp_path.
"""

import hashlib
import json
import shutil
import sys
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.app import create_app
from api.security import TOKEN_ENV_VAR
from api.storage import ApiSettings, library_config, library_control_file, library_dir
from src import db
from src.utils import Photo, photo_id_for

TOKEN = "test-session-token-0123456789"
HOSTS = ("127.0.0.1", "localhost")
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LIB = "run-library"
OTHER_LIB = "other-library"
WAIT_SECONDS = 20.0

# The stand-in engine. The first argument picks a behavior; the rest is what the supervisor
# appends (`analyze --input ... --data-root ...`). It records its argv and environment in
# `argv.json` next to itself so tests can check how it was started.
STUB_ENGINE = r'''
import json, os, sys, time
from pathlib import Path

mode = sys.argv[1]
args = sys.argv[2:]
here = Path(__file__).resolve().parent
(here / "argv.json").write_text(
    json.dumps({"args": args, "token_in_env": "AURAFRAME_API_TOKEN" in os.environ}),
    encoding="utf-8",
)
data_root = Path(args[args.index("--data-root") + 1])
control = data_root / "working" / "control.json"


def emit(event):
    print(json.dumps(event), flush=True)


def flags():
    try:
        data = json.loads(control.read_text(encoding="utf-8"))
        return bool(data.get("paused")), bool(data.get("cancel"))
    except (OSError, ValueError):
        return False, False


if mode == "quick":
    emit({"type": "progress", "step": "indexing", "current": 1, "total": 2, "message": "Indexing"})
    print("plain text, not an event", flush=True)
    print("[1, 2, 3]", flush=True)
    emit({"message": "no type"})
    emit({"type": "Bad Type!", "message": "wrong type shape"})
    emit({"type": "log", "level": "info", "message": "x" * 5000})
    emit({"type": "log", "level": "info", "message": "reading " + args[args.index("--input") + 1]})
    emit({"type": "log", "level": "info", "message": "writing " + str(data_root)})
    emit({"type": "photo_scored", "photoId": "IMG_0123456789", "finalScore": 7.5, "category": "good"})
    emit({"type": "progress", "step": "quality", "current": 2, "total": 2, "message": "Scoring"})
    emit({"type": "done", "message": "All finished"})
elif mode == "silent":
    pass
elif mode == "fail":
    sys.exit(3)
elif mode == "wait":
    emit({"type": "progress", "step": "indexing", "current": 0, "total": 9, "message": "Waiting"})
    while not flags()[1]:
        time.sleep(0.05)
    time.sleep(0.5)  # stay alive briefly after a cancel so tests can observe "cancelling"
elif mode == "hang":
    emit({"type": "progress", "step": "indexing", "current": 0, "total": 9, "message": "Hanging"})
    time.sleep(120)
'''


@dataclass
class World:
    """A synthetic photos root, a client bound to a stub engine, and the paths around them."""

    client: TestClient
    settings: ApiSettings
    photos: list[Path]
    photo_ids: list[str]
    outside: Path
    stub_dir: Path

    # Returns the argv and environment facts the stub recorded for its last start.
    def stub_record(self) -> dict[str, Any]:
        return json.loads((self.stub_dir / "argv.json").read_text(encoding="utf-8"))


WorldFactory = Callable[..., World]


# Writes a small synthetic JPEG filled with one color and returns its path.
def _jpeg(path: Path, color: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.full((64, 96, 3), color, dtype="uint8")
    Image.fromarray(pixels).save(path, "JPEG", quality=90)
    return path


# Returns the SHA-256 of a file, to prove originals are not rewritten.
def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Stores one photo row the way the engine does, through db.upsert_photo.
def _store(settings: ApiSettings, library_id: str, original: Path) -> str:
    photo_id = photo_id_for(original)
    photo = Photo(
        id=photo_id,
        file_path=str(original),
        file_name=original.name,
        width=96,
        height=64,
    )
    with closing(db.connect(library_config(settings, library_id))) as conn:
        db.upsert_photo(conn, photo)
    return photo_id


# Splits an SSE body into the JSON events of its `data:` frames.
def _frames(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


# Polls the run status until the engine reports it is no longer running.
def _wait_stopped(client: TestClient, library_id: str = LIB) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        status = client.get(f"/api/libraries/{library_id}/runs/current").json()
        if not status["running"]:
            return status
        time.sleep(0.05)
    raise AssertionError("the run did not stop in time")


# Reads the whole event stream of a library; it returns once the run has ended.
def _read_events(client: TestClient, library_id: str = LIB) -> list[dict[str, Any]]:
    response = client.get(f"/api/libraries/{library_id}/events")
    assert response.status_code == 200
    return _frames(response.text)


@pytest.fixture()
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return a factory that builds a World around a stub engine mode, and stops it afterwards."""
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN)
    apps = []

    def build(mode: str = "quick", engine_command: tuple[str, ...] | None = None) -> World:
        stub_dir = tmp_path / "stub"
        stub_dir.mkdir(exist_ok=True)
        stub = stub_dir / "stub_engine.py"
        stub.write_text(STUB_ENGINE, encoding="utf-8")
        photos_root = tmp_path / "photos"
        photos = [_jpeg(photos_root / "a.jpg", 40), _jpeg(photos_root / "sub" / "b.jpg", 200)]
        settings = ApiSettings(
            data_root=tmp_path / "data",
            photos_root=photos_root,
            engine_command=engine_command or (sys.executable, str(stub), mode),
        )
        ids = [_store(settings, LIB, photo) for photo in photos]
        outside = _jpeg(tmp_path / "outside" / "secret.jpg", 7)
        app = create_app(TOKEN, HOSTS, settings)
        apps.append(app)
        client = TestClient(app, base_url="http://127.0.0.1:8000", headers=AUTH)
        return World(client, settings, photos, ids, outside, stub_dir)

    yield build
    for app in apps:
        app.state.runs.shutdown()


# --- runs and events ------------------------------------------------------------------------


def test_a_run_streams_the_engine_events_in_order(make_world: WorldFactory) -> None:
    world = make_world("quick")
    started = world.client.post(f"/api/libraries/{LIB}/runs")
    assert started.status_code == 202
    body = started.json()
    assert body["libraryId"] == LIB
    assert body["running"] is True
    assert "startedAt" in body

    response = world.client.get(f"/api/libraries/{LIB}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    events = _frames(response.text)

    assert [e["type"] for e in events] == [
        "progress", "log", "log", "photo_scored", "progress", "done",
    ]  # fmt: skip
    assert all(e["libraryId"] == LIB for e in events)
    assert events[3]["photoId"] == "IMG_0123456789"
    assert events[-1]["message"] == "All finished"

    final = _wait_stopped(world.client)
    assert final["step"] == "done"
    assert final["current"] == 2 and final["total"] == 2
    assert final["running"] is False
    assert "finishedAt" in final


def test_events_hide_server_paths_and_drop_malformed_lines(make_world: WorldFactory) -> None:
    world = make_world("quick")
    world.client.post(f"/api/libraries/{LIB}/runs")
    body = world.client.get(f"/api/libraries/{LIB}/events").text
    assert str(world.settings.data_root) not in body
    assert str(world.settings.photos_root) not in body
    assert "<photos>" in body and "<data>" in body
    assert "not an event" not in body
    assert "no type" not in body
    assert "wrong type shape" not in body
    assert "x" * 100 not in body


def test_the_engine_is_started_from_validated_options_only(make_world: WorldFactory) -> None:
    world = make_world("quick")
    world.client.post(f"/api/libraries/{LIB}/runs")
    _wait_stopped(world.client)
    record = world.stub_record()
    args = record["args"]
    assert args[0] == "analyze"
    assert args[args.index("--input") + 1] == str(world.settings.photos_root)
    assert args[args.index("--data-root") + 1] == str(library_dir(world.settings, LIB))
    assert args[args.index("--preset") + 1] == "balanced"
    assert "--gemma" not in args and "--no-subfolders" not in args


def test_run_options_become_engine_flags(make_world: WorldFactory) -> None:
    world = make_world("quick")
    options = {"runGemma": True, "preset": "quality", "includeSubfolders": False}
    assert world.client.post(f"/api/libraries/{LIB}/runs", json=options).status_code == 202
    _wait_stopped(world.client)
    args = world.stub_record()["args"]
    assert args[args.index("--preset") + 1] == "quality"
    assert "--gemma" in args and "--no-subfolders" in args


def test_the_engine_does_not_receive_the_api_token(make_world: WorldFactory) -> None:
    world = make_world("quick")
    world.client.post(f"/api/libraries/{LIB}/runs")
    _wait_stopped(world.client)
    assert world.stub_record()["token_in_env"] is False


def test_a_second_run_is_refused_while_one_is_active(make_world: WorldFactory) -> None:
    world = make_world("wait")
    assert world.client.post(f"/api/libraries/{LIB}/runs").status_code == 202
    again = world.client.post(f"/api/libraries/{LIB}/runs")
    assert again.status_code == 409
    assert again.json()["code"] == "run_in_progress"
    other = world.client.post(f"/api/libraries/{OTHER_LIB}/runs")
    assert other.status_code == 409
    world.client.post(f"/api/libraries/{LIB}/runs/current/cancel")
    _wait_stopped(world.client)


def test_a_new_run_can_start_after_the_previous_one_ended(make_world: WorldFactory) -> None:
    world = make_world("quick")
    world.client.post(f"/api/libraries/{LIB}/runs")
    _read_events(world.client)  # returns only after the process has exited
    again = world.client.post(f"/api/libraries/{LIB}/runs")
    assert again.status_code == 202
    assert [e["type"] for e in _read_events(world.client)][-1] == "done"


def test_status_of_a_library_that_never_ran_is_idle(make_world: WorldFactory) -> None:
    world = make_world("wait")
    idle = world.client.get(f"/api/libraries/{LIB}/runs/current")
    assert idle.status_code == 200
    assert idle.json() == {
        "libraryId": LIB, "step": "idle", "running": False, "paused": False,
        "current": 0, "total": 0, "message": "Idle",
    }  # fmt: skip

    world.client.post(f"/api/libraries/{LIB}/runs")
    running = world.client.get(f"/api/libraries/{LIB}/runs/current").json()
    assert running["running"] is True
    elsewhere = world.client.get(f"/api/libraries/{OTHER_LIB}/runs/current").json()
    assert elsewhere["running"] is False and elsewhere["libraryId"] == OTHER_LIB
    world.client.post(f"/api/libraries/{LIB}/runs/current/cancel")
    _wait_stopped(world.client)


def test_pause_resume_and_cancel_drive_the_control_file(make_world: WorldFactory) -> None:
    world = make_world("wait")
    world.client.post(f"/api/libraries/{LIB}/runs")
    control = library_control_file(world.settings, LIB)
    assert json.loads(control.read_text(encoding="utf-8")) == {"paused": False, "cancel": False}

    paused = world.client.post(f"/api/libraries/{LIB}/runs/current/pause")
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert json.loads(control.read_text(encoding="utf-8")) == {"paused": True, "cancel": False}
    assert world.client.get(f"/api/libraries/{LIB}/runs/current").json()["paused"] is True

    resumed = world.client.post(f"/api/libraries/{LIB}/runs/current/resume")
    assert resumed.json()["paused"] is False
    assert json.loads(control.read_text(encoding="utf-8")) == {"paused": False, "cancel": False}

    cancelling = world.client.post(f"/api/libraries/{LIB}/runs/current/cancel")
    assert cancelling.status_code == 200
    assert json.loads(control.read_text(encoding="utf-8")) == {"paused": False, "cancel": True}
    blocked = world.client.post(f"/api/libraries/{LIB}/runs/current/pause")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "run_cancelling"

    final = _wait_stopped(world.client)
    assert final["step"] == "idle"
    assert final["message"] == "Analysis cancelled."
    events = _read_events(world.client)
    assert events[-1]["message"] == "Analysis cancelled."


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_control_routes_need_an_active_run(make_world: WorldFactory, action: str) -> None:
    world = make_world("quick")
    never = world.client.post(f"/api/libraries/{LIB}/runs/current/{action}")
    assert never.status_code == 409
    assert never.json()["code"] == "no_active_run"

    world.client.post(f"/api/libraries/{LIB}/runs")
    _read_events(world.client)
    finished = world.client.post(f"/api/libraries/{LIB}/runs/current/{action}")
    assert finished.status_code == 409
    assert finished.json()["code"] == "no_active_run"


def test_control_routes_refuse_another_librarys_run(make_world: WorldFactory) -> None:
    world = make_world("wait")
    world.client.post(f"/api/libraries/{LIB}/runs")
    response = world.client.post(f"/api/libraries/{OTHER_LIB}/runs/current/cancel")
    assert response.status_code == 409
    world.client.post(f"/api/libraries/{LIB}/runs/current/cancel")
    _wait_stopped(world.client)


def test_a_stale_cancel_does_not_stop_the_next_run(make_world: WorldFactory) -> None:
    world = make_world("wait")
    control = library_control_file(world.settings, LIB)
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text(json.dumps({"paused": True, "cancel": True}), encoding="utf-8")
    world.client.post(f"/api/libraries/{LIB}/runs")
    assert json.loads(control.read_text(encoding="utf-8")) == {"paused": False, "cancel": False}
    assert world.client.get(f"/api/libraries/{LIB}/runs/current").json()["running"] is True
    world.client.post(f"/api/libraries/{LIB}/runs/current/cancel")
    _wait_stopped(world.client)


def test_an_engine_that_ignores_cancel_is_killed(make_world: WorldFactory) -> None:
    world = make_world("hang")
    world.client.app.state.runs.cancel_grace_seconds = 0.3
    world.client.post(f"/api/libraries/{LIB}/runs")
    world.client.post(f"/api/libraries/{LIB}/runs/current/cancel")
    final = _wait_stopped(world.client)
    assert final["message"] == "Analysis cancelled."


def test_a_failing_engine_ends_the_stream_with_an_error(make_world: WorldFactory) -> None:
    world = make_world("fail")
    world.client.post(f"/api/libraries/{LIB}/runs")
    events = _read_events(world.client)
    assert [e["type"] for e in events] == ["error"]
    final = _wait_stopped(world.client)
    assert final["step"] == "error"
    assert "stopped unexpectedly" in final["message"]


def test_an_engine_that_exits_quietly_is_reported_as_done(make_world: WorldFactory) -> None:
    world = make_world("silent")
    world.client.post(f"/api/libraries/{LIB}/runs")
    assert [e["type"] for e in _read_events(world.client)] == ["done"]


def test_a_late_client_still_receives_the_whole_run(make_world: WorldFactory) -> None:
    world = make_world("quick")
    world.client.post(f"/api/libraries/{LIB}/runs")
    _wait_stopped(world.client)
    first = _read_events(world.client)
    second = _read_events(world.client)
    assert first == second and first[-1]["type"] == "done"


def test_events_need_a_run_of_that_library(make_world: WorldFactory) -> None:
    world = make_world("quick")
    before = world.client.get(f"/api/libraries/{LIB}/events")
    assert before.status_code == 404
    assert before.json()["code"] == "no_run"
    world.client.post(f"/api/libraries/{LIB}/runs")
    _read_events(world.client)
    other = world.client.get(f"/api/libraries/{OTHER_LIB}/events")
    assert other.status_code == 404


def test_a_missing_engine_command_is_a_structured_503(
    make_world: WorldFactory, tmp_path: Path
) -> None:
    world = make_world(engine_command=(str(tmp_path / "no-such-engine"),))
    response = world.client.post(f"/api/libraries/{LIB}/runs")
    assert response.status_code == 503
    assert response.json()["code"] == "engine_unavailable"
    assert str(tmp_path) not in response.text
    assert world.client.get(f"/api/libraries/{LIB}/runs/current").json()["running"] is False


def test_a_run_needs_the_photos_root(make_world: WorldFactory) -> None:
    world = make_world("quick")
    shutil.rmtree(world.settings.photos_root)
    response = world.client.post(f"/api/libraries/{LIB}/runs")
    assert response.status_code == 409
    assert response.json()["code"] == "photos_unavailable"


@pytest.mark.parametrize(
    "body",
    [
        {"inputFolder": "/etc"},
        {"path": "../escape"},
        {"preset": "turbo"},
        {"preset": "../../bin/sh"},
        {"runGemma": "yes"},
        {"includeSubfolders": 1},
        {"engineCommand": ["rm", "-rf", "/"]},
    ],
)
def test_run_bodies_are_validated(make_world: WorldFactory, body: dict[str, Any]) -> None:
    world = make_world("quick")
    response = world.client.post(f"/api/libraries/{LIB}/runs", json=body)
    assert response.status_code == 422
    assert not (world.stub_dir / "argv.json").exists()
    assert world.client.get(f"/api/libraries/{LIB}/runs/current").json()["step"] == "idle"


@pytest.mark.parametrize("library_id", ["-lead", "bad.id", "has space", "x" * 65, "..%2F.."])
def test_run_routes_reject_invalid_library_ids(make_world: WorldFactory, library_id: str) -> None:
    world = make_world("quick")
    for method, suffix in [
        ("POST", "runs"),
        ("GET", "runs/current"),
        ("POST", "runs/current/pause"),
        ("POST", "runs/current/resume"),
        ("POST", "runs/current/cancel"),
        ("GET", "events"),
    ]:
        response = world.client.request(method, f"/api/libraries/{library_id}/{suffix}")
        assert response.status_code in (404, 422), (method, suffix)
    assert not (world.stub_dir / "argv.json").exists()


# --- exports --------------------------------------------------------------------------------


def test_export_copies_the_originals_into_the_library_output(make_world: WorldFactory) -> None:
    world = make_world()
    before = [_digest(p) for p in world.photos]
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": world.photo_ids})
    assert response.status_code == 200
    assert response.json() == {"exported": 2}
    assert "/" not in response.text

    folder = library_dir(world.settings, LIB) / "output" / "top_06_final_set"
    copies = sorted(folder.iterdir())
    assert [p.name for p in copies] == [f"01_{world.photo_ids[0]}.jpg", f"02_{world.photo_ids[1]}.jpg"]
    assert [_digest(p) for p in copies] == before
    assert [_digest(p) for p in world.photos] == before
    assert all(p.exists() for p in world.photos)


def test_export_follows_request_order_and_ignores_repeated_ids(make_world: WorldFactory) -> None:
    world = make_world()
    ids = [world.photo_ids[1], world.photo_ids[0], world.photo_ids[1]]
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": ids})
    assert response.json() == {"exported": 2}
    folder = library_dir(world.settings, LIB) / "output" / "top_06_final_set"
    assert sorted(p.name for p in folder.iterdir()) == [
        f"01_{world.photo_ids[1]}.jpg",
        f"02_{world.photo_ids[0]}.jpg",
    ]


def test_exporting_twice_overwrites_only_the_copies(make_world: WorldFactory) -> None:
    world = make_world()
    before = [_digest(p) for p in world.photos]
    for _ in range(2):
        response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": world.photo_ids})
        assert response.status_code == 200
    assert [_digest(p) for p in world.photos] == before


def test_export_refuses_an_unknown_id_and_copies_nothing(make_world: WorldFactory) -> None:
    world = make_world()
    ids = [world.photo_ids[0], "IMG_0000000000"]
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": ids})
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_photo_ids"
    assert not (library_dir(world.settings, LIB) / "output").exists()


def test_export_of_a_library_without_data_is_refused(make_world: WorldFactory) -> None:
    world = make_world()
    response = world.client.post(
        f"/api/libraries/{OTHER_LIB}/exports", json={"ids": world.photo_ids}
    )
    assert response.status_code == 422
    assert not library_dir(world.settings, OTHER_LIB).exists()


@pytest.mark.parametrize(
    "ids",
    [
        [],
        ["../../etc/passwd"],
        ["img_0123456789"],
        ["IMG_0123456789 "],
        ["IMG_01234567890"],
        ["/photos/a.jpg"],
        [1],
        "IMG_0123456789",
        ["IMG_0123456789"] * 501,
    ],
)
def test_export_ids_are_validated(make_world: WorldFactory, ids: Any) -> None:
    world = make_world()
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": ids})
    assert response.status_code == 422
    assert not (library_dir(world.settings, LIB) / "output").exists()


@pytest.mark.parametrize(
    "extra",
    [{"path": "/tmp"}, {"destination": "../out"}, {"folder": "x"}],
)
def test_export_bodies_reject_extra_fields(make_world: WorldFactory, extra: dict[str, str]) -> None:
    world = make_world()
    response = world.client.post(
        f"/api/libraries/{LIB}/exports", json={"ids": world.photo_ids, **extra}
    )
    assert response.status_code == 422
    assert not (library_dir(world.settings, LIB) / "output").exists()


def test_export_refuses_a_photo_outside_the_photos_root(make_world: WorldFactory) -> None:
    world = make_world()
    stray = _store(world.settings, LIB, world.outside)
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": [stray]})
    assert response.status_code == 422
    assert response.json()["code"] == "photo_unavailable"
    assert not (library_dir(world.settings, LIB) / "output").exists()


def test_export_refuses_a_photo_whose_file_is_gone(make_world: WorldFactory) -> None:
    world = make_world()
    world.photos[0].unlink()
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": world.photo_ids})
    assert response.status_code == 422
    assert response.json()["code"] == "photo_unavailable"
    assert not (library_dir(world.settings, LIB) / "output").exists()


def test_export_never_writes_through_a_symlinked_output_folder(
    make_world: WorldFactory, tmp_path: Path
) -> None:
    world = make_world()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    output = library_dir(world.settings, LIB) / "output"
    output.mkdir(parents=True)
    (output / "top_06_final_set").symlink_to(elsewhere, target_is_directory=True)
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": world.photo_ids})
    assert response.status_code == 409
    assert response.json()["code"] == "export_folder_unsafe"
    assert list(elsewhere.iterdir()) == []


def test_export_does_not_follow_a_symlinked_target_file(
    make_world: WorldFactory, tmp_path: Path
) -> None:
    world = make_world()
    victim = tmp_path / "victim.jpg"
    victim.write_bytes(b"keep me")
    folder = library_dir(world.settings, LIB) / "output" / "top_06_final_set"
    folder.mkdir(parents=True)
    (folder / f"01_{world.photo_ids[0]}.jpg").symlink_to(victim)
    response = world.client.post(f"/api/libraries/{LIB}/exports", json={"ids": world.photo_ids})
    assert response.status_code == 200
    assert victim.read_bytes() == b"keep me"


# --- guard ----------------------------------------------------------------------------------

GUARDED = [
    ("POST", f"/api/libraries/{LIB}/runs"),
    ("GET", f"/api/libraries/{LIB}/runs/current"),
    ("POST", f"/api/libraries/{LIB}/runs/current/pause"),
    ("POST", f"/api/libraries/{LIB}/runs/current/resume"),
    ("POST", f"/api/libraries/{LIB}/runs/current/cancel"),
    ("GET", f"/api/libraries/{LIB}/events"),
    ("POST", f"/api/libraries/{LIB}/exports"),
]


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_run_routes_need_the_session_token(make_world: WorldFactory, method: str, path: str) -> None:
    world = make_world("quick")
    anonymous = TestClient(world.client.app, base_url="http://127.0.0.1:8000")
    response = anonymous.request(method, path)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert not (world.stub_dir / "argv.json").exists()


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_run_routes_refuse_a_foreign_host(make_world: WorldFactory, method: str, path: str) -> None:
    world = make_world("quick")
    response = world.client.request(method, path, headers={"Host": "evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "foreign_host"
    assert not (world.stub_dir / "argv.json").exists()
