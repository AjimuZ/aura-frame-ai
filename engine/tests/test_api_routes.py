"""Tests for the /api routes: photos, media by photo ID, providers, data and review.

Every image is synthetic and lives under pytest's tmp_path; no personal photo is read.
"""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.app import create_app
from api.storage import (
    ApiSettings,
    library_config,
    library_db_file,
    library_dir,
    library_exports_dir,
    library_thumbs_dir,
)
from src import db
from src.utils import Photo, photo_id_for

TOKEN = "test-session-token-0123456789"
HOSTS = ("127.0.0.1", "localhost")
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LIB = "11111111-2222-3333-4444-555555555555"
OTHER_LIB = "other-library"


@dataclass
class World:
    """A synthetic photos root, a data root and a client bound to them."""

    client: TestClient
    settings: ApiSettings
    photos: list[Path]
    thumbs: list[Path]
    outside: Path


# Writes a small synthetic JPEG filled with one color and returns its path.
def _jpeg(path: Path, color: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.full((64, 96, 3), color, dtype="uint8")
    Image.fromarray(pixels).save(path, "JPEG", quality=90)
    return path


# Stores one photo row the way the engine does, through db.upsert_photo.
def _store(settings: ApiSettings, library_id: str, photo: Photo) -> None:
    with closing(db.connect(library_config(settings, library_id))) as conn:
        db.upsert_photo(conn, photo)


@pytest.fixture()
def world(tmp_path: Path) -> World:
    photos_root = tmp_path / "photos"
    settings = ApiSettings(
        data_root=tmp_path / "data", photos_root=photos_root, exports_root=tmp_path / "exports"
    )
    photos = [_jpeg(photos_root / "a.jpg", 40), _jpeg(photos_root / "sub" / "b.jpg", 200)]
    thumbs = [
        _jpeg(library_thumbs_dir(settings, LIB) / "a.jpg", 41),
        _jpeg(library_thumbs_dir(settings, LIB) / "b.jpg", 201),
    ]
    for original, thumb in zip(photos, thumbs):
        _store(
            settings,
            LIB,
            Photo(
                id=photo_id_for(original),
                file_path=str(original),
                file_name=original.name,
                thumbnail_path=str(thumb),
                width=96,
                height=64,
            ),
        )
    outside = _jpeg(tmp_path / "outside" / "secret.jpg", 7)
    app = create_app(TOKEN, HOSTS, settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000", headers=AUTH)
    return World(client, settings, photos, thumbs, outside)


# The guarded routes, with a valid library and photo shape, for the guard tests.
GUARDED = [
    ("GET", f"/api/libraries/{LIB}/photos"),
    ("GET", f"/api/libraries/{LIB}/media/IMG_0123456789"),
    ("GET", "/api/providers"),
    ("DELETE", f"/api/libraries/{LIB}/data"),
    ("GET", f"/api/libraries/{LIB}/review"),
    ("PUT", f"/api/libraries/{LIB}/review"),
]
SESSION = {
    "slotAssignments": {"cover": "IMG_0123456789"},
    "locks": {"cover": True},
    "shortlist": ["IMG_0123456789", "IMG_ABCDEF0123"],
}


# --- photos ---------------------------------------------------------------------------------


def test_get_photos_lists_the_library(world: World) -> None:
    response = world.client.get(f"/api/libraries/{LIB}/photos")
    assert response.status_code == 200
    records = response.json()
    assert {r["id"] for r in records} == {photo_id_for(p) for p in world.photos}
    assert {r["fileName"] for r in records} == {"a.jpg", "b.jpg"}


def test_get_photos_of_an_unknown_library_is_empty(world: World) -> None:
    response = world.client.get(f"/api/libraries/{OTHER_LIB}/photos")
    assert response.status_code == 200
    assert response.json() == []
    assert not library_db_file(world.settings, OTHER_LIB).exists()


@pytest.mark.parametrize("library_id", ["-lead", "bad.id", "has space", "x" * 65, "a%2Fb", "..%2F.."])
def test_invalid_library_id_is_refused(world: World, library_id: str) -> None:
    for method, template in (
        ("GET", "/api/libraries/{}/photos"),
        ("GET", "/api/libraries/{}/review"),
        ("DELETE", "/api/libraries/{}/data"),
        ("GET", "/api/libraries/{}/media/IMG_0123456789"),
    ):
        response = world.client.request(method, template.format(library_id))
        assert response.status_code in (404, 422)
        assert response.status_code != 200


# --- media ----------------------------------------------------------------------------------


def test_media_serves_the_thumbnail_by_photo_id(world: World) -> None:
    photo_id = photo_id_for(world.photos[0])
    response = world.client.get(f"/api/libraries/{LIB}/media/{photo_id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content == world.thumbs[0].read_bytes()


def test_media_serves_the_original_on_request(world: World) -> None:
    photo_id = photo_id_for(world.photos[1])
    response = world.client.get(f"/api/libraries/{LIB}/media/{photo_id}", params={"variant": "original"})
    assert response.status_code == 200
    assert response.content == world.photos[1].read_bytes()


def test_media_with_an_unknown_variant_is_422(world: World) -> None:
    photo_id = photo_id_for(world.photos[0])
    response = world.client.get(f"/api/libraries/{LIB}/media/{photo_id}", params={"variant": "raw"})
    assert response.status_code == 422


def test_media_unknown_photo_id_is_404(world: World) -> None:
    response = world.client.get(f"/api/libraries/{LIB}/media/IMG_0000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "photo_not_found"


def test_media_in_an_unknown_library_is_404(world: World) -> None:
    photo_id = photo_id_for(world.photos[0])
    response = world.client.get(f"/api/libraries/{OTHER_LIB}/media/{photo_id}")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "photo_id",
    [
        "img_0123456789", "IMG_01234", "IMG_0123456789AB", "IMG_012345678G", "abc", "..", "%2e%2e",
        "..%2F..%2Fa.jpg", "0" * 63, "0" * 65, "A" * 64, "g" * 64,
    ],
)
def test_media_malformed_photo_id_is_never_served(world: World, photo_id: str) -> None:
    response = world.client.get(f"/api/libraries/{LIB}/media/{photo_id}")
    assert response.status_code in (404, 422)
    assert response.headers.get("content-type", "").startswith("image/") is False


def test_media_refuses_a_stored_path_outside_the_expected_root(world: World) -> None:
    hostile = Photo(
        id="IMG_AAAAAAAAAA",
        file_path=str(world.outside),
        file_name="secret.jpg",
        thumbnail_path=str(world.outside),
    )
    _store(world.settings, LIB, hostile)
    for variant in ("thumbnail", "original"):
        response = world.client.get(f"/api/libraries/{LIB}/media/IMG_AAAAAAAAAA", params={"variant": variant})
        assert response.status_code == 404
        assert response.json()["code"] == "media_unavailable"


def test_media_refuses_a_dot_dot_path_that_escapes_the_root(world: World) -> None:
    sneaky = str(world.photos[0].parent / ".." / "outside" / "secret.jpg")
    _store(world.settings, LIB, Photo(id="IMG_BBBBBBBBBB", file_path=sneaky, file_name="s.jpg"))
    response = world.client.get(
        f"/api/libraries/{LIB}/media/IMG_BBBBBBBBBB", params={"variant": "original"}
    )
    assert response.status_code == 404


def test_media_refuses_a_non_image_extension(world: World) -> None:
    note = world.photos[0].parent / "notes.txt"
    note.write_text("not an image", encoding="utf-8")
    _store(world.settings, LIB, Photo(id="IMG_CCCCCCCCCC", file_path=str(note), file_name="notes.txt"))
    response = world.client.get(
        f"/api/libraries/{LIB}/media/IMG_CCCCCCCCCC", params={"variant": "original"}
    )
    assert response.status_code == 404


def test_missing_thumbnail_is_404(world: World) -> None:
    world.thumbs[0].unlink()
    response = world.client.get(f"/api/libraries/{LIB}/media/{photo_id_for(world.photos[0])}")
    assert response.status_code == 404
    assert response.json()["code"] == "media_unavailable"


def test_no_route_accepts_a_path(world: World) -> None:
    allowed_path_params = {"library_id", "photo_id"}
    allowed_query_params = {"variant"}
    for route in world.client.app.routes:  # type: ignore[attr-defined]
        dependant = getattr(route, "dependant", None)
        if dependant is None or not route.path.startswith("/api"):
            continue
        assert {p.name for p in dependant.path_params} <= allowed_path_params
        assert {p.name for p in dependant.query_params} <= allowed_query_params
        assert "{path" not in route.path and ":path}" not in route.path


# --- providers ------------------------------------------------------------------------------


def test_providers_report_a_minimal_status_shape(world: World) -> None:
    response = world.client.get("/api/providers")
    assert response.status_code == 200
    providers = {p["id"]: p for p in response.json()["providers"]}
    assert providers["local-scoring"]["state"] == "available"
    assert providers["local-scoring"]["cloud"] is False
    assert providers["llama-server"]["state"] == "not_configured"
    assert providers["codex"]["state"] == "disabled"
    assert providers["codex"]["cloud"] is True
    assert set(providers) == {"local-scoring", "llama-server", "codex"}
    for provider in providers.values():
        assert set(provider) == {"id", "label", "cloud", "state", "detail"}


# --- data -----------------------------------------------------------------------------------


def test_delete_data_removes_only_generated_data(world: World) -> None:
    base = library_dir(world.settings, LIB)
    shown = library_exports_dir(world.settings, LIB)
    (shown / "output").mkdir(parents=True)
    (shown / "output" / "export.txt").write_text("copy", encoding="utf-8")
    (shown / "reports").mkdir()
    sibling = library_dir(world.settings, OTHER_LIB) / "working"
    sibling.mkdir(parents=True)
    sibling_export = library_exports_dir(world.settings, OTHER_LIB) / "output"
    sibling_export.mkdir(parents=True)
    before = [p.read_bytes() for p in world.photos + [world.outside]]

    response = world.client.delete(f"/api/libraries/{LIB}/data")

    assert response.status_code == 200
    assert response.json() == {"removed": 3}
    assert not (base / "working").exists()
    assert not (shown / "output").exists()
    assert not (shown / "reports").exists()
    assert sibling.is_dir()
    assert sibling_export.is_dir()
    assert [p.read_bytes() for p in world.photos + [world.outside]] == before
    assert world.client.get(f"/api/libraries/{LIB}/photos").json() == []


def test_delete_data_is_idempotent(world: World) -> None:
    assert world.client.delete(f"/api/libraries/{LIB}/data").json() == {"removed": 1}
    assert world.client.delete(f"/api/libraries/{LIB}/data").json() == {"removed": 0}
    assert world.client.delete(f"/api/libraries/{OTHER_LIB}/data").json() == {"removed": 0}


def test_delete_data_does_not_follow_a_symlink_out_of_the_library(world: World, tmp_path: Path) -> None:
    base = library_dir(world.settings, OTHER_LIB)
    base.mkdir(parents=True)
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    (base / "output").symlink_to(target, target_is_directory=True)
    world.client.delete(f"/api/libraries/{OTHER_LIB}/data")
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (base / "output").exists()


def test_delete_data_does_not_follow_a_symlink_in_the_exports_folder(
    world: World, tmp_path: Path
) -> None:
    shown = library_exports_dir(world.settings, OTHER_LIB)
    shown.mkdir(parents=True)
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    (shown / "reports").symlink_to(target, target_is_directory=True)
    world.client.delete(f"/api/libraries/{OTHER_LIB}/data")
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (shown / "reports").exists()


def test_the_library_config_puts_copies_and_reports_under_the_exports_root(world: World) -> None:
    output = library_config(world.settings, LIB)["output"]
    shown = world.settings.exports_root / LIB
    working = library_dir(world.settings, LIB) / "working"
    assert Path(output["base_dir"]) == shown / "output"
    assert Path(output["reports_dir"]) == shown / "reports"
    assert Path(output["working_dir"]) == working
    assert Path(output["db_path"]) == working / "auraframe.db"


# --- review ---------------------------------------------------------------------------------


def test_review_is_null_before_the_first_save(world: World) -> None:
    response = world.client.get(f"/api/libraries/{OTHER_LIB}/review")
    assert response.status_code == 200
    assert response.json() is None


def test_review_round_trips_and_keeps_the_latest(world: World) -> None:
    put = world.client.put(f"/api/libraries/{LIB}/review", json=SESSION)
    assert put.status_code == 200
    assert put.json() == SESSION
    assert world.client.get(f"/api/libraries/{LIB}/review").json() == SESSION

    newer = {**SESSION, "shortlist": ["IMG_ABCDEF0123"], "locks": {}}
    assert world.client.put(f"/api/libraries/{LIB}/review", json=newer).status_code == 200
    assert world.client.get(f"/api/libraries/{LIB}/review").json() == newer


def test_review_put_creates_the_library_data_in_the_data_root_only(world: World) -> None:
    assert world.client.put(f"/api/libraries/{OTHER_LIB}/review", json=SESSION).status_code == 200
    assert library_db_file(world.settings, OTHER_LIB).is_file()
    assert library_db_file(world.settings, OTHER_LIB).resolve().is_relative_to(world.settings.data_root)


@pytest.mark.parametrize(
    "body",
    [
        {**SESSION, "extra": 1},
        {"slotAssignments": {}, "locks": {}},
        {**SESSION, "shortlist": "IMG_0123456789"},
        {**SESSION, "locks": {"cover": "yes"}},
        {**SESSION, "slotAssignments": {"cover": 7}},
        {**SESSION, "shortlist": [""]},
        {**SESSION, "shortlist": ["x" * 129]},
        {**SESSION, "slotAssignments": {"": "IMG_0123456789"}},
        [],
        "text",
    ],
)
def test_review_put_rejects_an_invalid_body(world: World, body: object) -> None:
    response = world.client.put(f"/api/libraries/{LIB}/review", json=body)
    assert response.status_code == 422
    assert world.client.get(f"/api/libraries/{LIB}/review").json() is None


def test_review_put_rejects_malformed_json(world: World) -> None:
    response = world.client.put(
        f"/api/libraries/{LIB}/review", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


# --- guard coverage -------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_new_routes_need_the_session_token(world: World, method: str, path: str) -> None:
    anonymous = TestClient(world.client.app, base_url="http://127.0.0.1:8000")
    response = anonymous.request(method, path, json=SESSION if method == "PUT" else None)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_new_routes_refuse_a_foreign_host(world: World, method: str, path: str) -> None:
    response = world.client.request(
        method, path, json=SESSION if method == "PUT" else None, headers={"Host": "evil.example"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "foreign_host"


# --- settings -------------------------------------------------------------------------------


def test_overlapping_roots_are_rejected(tmp_path: Path) -> None:
    far = tmp_path / "far"
    with pytest.raises(ValueError):
        ApiSettings(
            data_root=tmp_path / "photos" / "data", photos_root=tmp_path / "photos", exports_root=far
        )
    with pytest.raises(ValueError):
        ApiSettings(data_root=tmp_path, photos_root=tmp_path / "photos", exports_root=far)
    with pytest.raises(ValueError):
        ApiSettings(data_root=tmp_path / "same", photos_root=tmp_path / "same", exports_root=far)


def test_the_exports_root_may_not_overlap_the_photos_or_the_data_root(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    data = tmp_path / "data"
    for exports in (photos / "out", tmp_path, photos, data / "out", data):
        with pytest.raises(ValueError):
            ApiSettings(data_root=data, photos_root=photos, exports_root=exports)


def test_library_dir_rejects_an_invalid_id(tmp_path: Path) -> None:
    settings = ApiSettings(
        data_root=tmp_path / "data",
        photos_root=tmp_path / "photos",
        exports_root=tmp_path / "exports",
    )
    for bad in ("", "..", "../x", "a/b", "-a"):
        with pytest.raises(ValueError):
            library_dir(settings, bad)
        with pytest.raises(ValueError):
            library_exports_dir(settings, bad)
