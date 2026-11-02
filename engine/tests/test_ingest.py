"""Tests for ingest: content-hash IDs, upright working copies, one decode, minimal EXIF.

Every image is synthetic and lives under pytest's tmp_path; no personal photo is read.
"""

import builtins
import hashlib
import io
import json
import struct
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image

from src import db
from src.embeddings import compute_embedding
from src.index_photos import index_photos
from src.ingest import ingest_image
from src.quality_score import score_quality
from src.utils import (
    Photo,
    file_sha256,
    photo_id_for,
    read_working_bgr,
    thumbnail_path_for,
    working_copy_path,
)

ORIENTATION = 0x0112
EXIF_IFD = 0x8769
GPS_IFD = 0x8825


# Writes a textured RGB JPEG of the given size (distinct per seed) and returns its path.
def _textured(path: Path, width: int, height: int, seed: int = 0, exif: bytes | None = None) -> Path:
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    options: dict[str, Any] = {"exif": exif} if exif is not None else {}
    Image.fromarray(pixels).save(path, "JPEG", quality=90, **options)
    return path


# Counts how often each file is decoded: Image.open and cv2.imread are wrapped and record the
# path they are given. Opens of in-memory buffers are not recorded.
class DecodeRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.pillow: list[Path] = []
        self.opencv: list[Path] = []
        real_open, real_imread = Image.open, cv2.imread

        def open_recorded(fp: Any, *args: Any, **kwargs: Any) -> Any:
            if not isinstance(fp, io.IOBase):
                self.pillow.append(Path(fp).resolve())
            return real_open(fp, *args, **kwargs)

        def imread_recorded(filename: str, *args: Any) -> Any:
            self.opencv.append(Path(filename).resolve())
            return real_imread(filename, *args)

        monkeypatch.setattr(Image, "open", open_recorded)
        monkeypatch.setattr(cv2, "imread", imread_recorded)

    # Returns how many times the file was decoded by either library.
    def decodes_of(self, path: Path) -> int:
        target = path.resolve()
        return self.pillow.count(target) + self.opencv.count(target)


# --- orientation ----------------------------------------------------------------------------


# An image stored sideways (EXIF orientation 6) gets an upright working copy and thumbnail, and
# neither carries an orientation tag that a later reader could apply a second time.
def test_orientation_6_gives_an_upright_working_copy_and_thumbnail(
    tmp_path: Path, data_config: dict
) -> None:
    pixels = np.zeros((400, 800, 3), dtype=np.uint8)
    pixels[:, :100] = 255  # the left strip of the stored image
    exif = Image.Exif()
    exif[ORIENTATION] = 6  # displayed rotated 90 degrees clockwise: the strip ends up on top
    path = tmp_path / "sideways.jpg"
    Image.fromarray(pixels).save(path, "JPEG", quality=95, exif=exif.tobytes())

    photo = ingest_image(path, data_config)

    assert (photo.width, photo.height) == (400, 800)
    sizes = {working_copy_path(data_config, photo.id): (400, 800), Path(photo.thumbnail_path): (256, 512)}
    for derived, expected_size in sizes.items():
        with Image.open(derived) as image:
            assert image.size == expected_size
            assert image.getexif().get(ORIENTATION) is None
            gray = np.asarray(image.convert("L"))
        rows = gray.shape[0]
        assert gray[rows // 50 : rows // 10].mean() > 200  # the top is bright
        assert gray[-rows // 10 : -rows // 50].mean() < 50  # the bottom is dark
    assert "Orientation" not in photo.exif


# --- sizes ----------------------------------------------------------------------------------


# A large original gets a working copy with a long side of 1,536 px and a 512 px thumbnail, both
# keeping the aspect ratio.
def test_working_copy_is_1536_and_thumbnail_is_512(tmp_path: Path, data_config: dict) -> None:
    path = _textured(tmp_path / "big.jpg", 3000, 2000)
    photo = ingest_image(path, data_config)

    assert (photo.width, photo.height) == (3000, 2000)
    with Image.open(working_copy_path(data_config, photo.id)) as working:
        assert working.size == (1536, 1024)
    with Image.open(photo.thumbnail_path) as thumb:
        assert thumb.size == (512, 341)


# A portrait original is bounded on its long (vertical) side.
def test_portrait_long_side_is_bounded(tmp_path: Path, data_config: dict) -> None:
    photo = ingest_image(_textured(tmp_path / "tall.jpg", 1800, 2400), data_config)
    with Image.open(working_copy_path(data_config, photo.id)) as working:
        assert working.size == (1152, 1536)
    with Image.open(photo.thumbnail_path) as thumb:
        assert max(thumb.size) == 512


# A small original is never upscaled.
def test_small_originals_are_not_upscaled(tmp_path: Path, data_config: dict) -> None:
    photo = ingest_image(_textured(tmp_path / "small.jpg", 400, 300), data_config)
    with Image.open(working_copy_path(data_config, photo.id)) as working:
        assert working.size == (400, 300)
    with Image.open(photo.thumbnail_path) as thumb:
        assert thumb.size == (400, 300)


# --- one decode -----------------------------------------------------------------------------


# Ingest, scoring and embedding together decode the original exactly once; every later stage
# reads the working copy instead.
def test_each_original_is_decoded_once(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _textured(tmp_path / "one.jpg", 2400, 1600)
    recorder = DecodeRecorder(monkeypatch)

    photo = ingest_image(path, data_config)
    score_quality(photo, data_config)
    compute_embedding(photo, None, data_config)

    assert recorder.decodes_of(path) == 1
    working = working_copy_path(data_config, photo.id)
    assert recorder.decodes_of(working) == 2  # quality (OpenCV) and embedding (Pillow)
    assert recorder.opencv == [working]


# The face stage also reads the working copy, never the original.
def test_face_stage_reads_the_working_copy(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mediapipe")
    from src.face_quality import score_faces

    path = _textured(tmp_path / "faces.jpg", 1200, 900)
    recorder = DecodeRecorder(monkeypatch)

    photo = ingest_image(path, data_config)
    score_faces(photo, data_config)

    assert recorder.decodes_of(path) == 1
    assert recorder.opencv == [working_copy_path(data_config, photo.id)]


# A stage reports a missing working copy instead of falling back to the original.
def test_a_missing_working_copy_is_not_replaced_by_the_original(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _textured(tmp_path / "gone.jpg", 600, 400)
    photo = ingest_image(path, data_config)
    working_copy_path(data_config, photo.id).unlink()
    recorder = DecodeRecorder(monkeypatch)

    assert read_working_bgr(photo, data_config) is None
    score_quality(photo, data_config)

    assert photo.status == "rejected" and photo.reject_reason == "unreadable_image"
    assert recorder.decodes_of(path) == 0


# --- content-hash IDs -----------------------------------------------------------------------


# The ID is the SHA-256 of the bytes, as 64 lower-case hex digits.
def test_the_photo_id_is_the_sha256_of_the_content(tmp_path: Path, data_config: dict) -> None:
    path = _textured(tmp_path / "a.jpg", 300, 200)
    photo = ingest_image(path, data_config)

    assert photo.id == hashlib.sha256(path.read_bytes()).hexdigest()
    assert photo.id == photo.file_hash == photo_id_for(path) == file_sha256(path)
    assert thumbnail_path_for(data_config, photo.id) == Path(photo.thumbnail_path)


# A moved or renamed file with the same bytes keeps its ID and its results, and is not decoded.
def test_a_moved_file_keeps_its_id_and_results(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "old").mkdir()
    old = _textured(tmp_path / "old" / "a.jpg", 800, 600)
    first = ingest_image(old, data_config)
    first.final_score = 7.5
    first.category = "STRONG"
    new = tmp_path / "new" / "renamed.jpg"
    new.parent.mkdir()
    old.rename(new)
    recorder = DecodeRecorder(monkeypatch)

    again = ingest_image(new, data_config, known={first.id: first})

    assert again.id == first.id
    assert again.carried_over
    assert (again.final_score, again.category) == (7.5, "STRONG")
    assert again.file_path == str(new) and again.file_name == "renamed.jpg"
    assert recorder.decodes_of(new) == 0
    assert not first.carried_over  # the stored record itself is untouched


# An unchanged file is skipped: nothing but the hash is computed.
def test_an_unchanged_file_is_skipped(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _textured(tmp_path / "same.jpg", 800, 600)
    first = ingest_image(path, data_config)
    recorder = DecodeRecorder(monkeypatch)

    again = ingest_image(path, data_config, known={first.id: first})

    assert again.carried_over and again.id == first.id
    assert recorder.decodes_of(path) == 0


# Changed bytes at the same path are a new photo with a new ID and are decoded again.
def test_changed_content_is_a_new_photo(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _textured(tmp_path / "edit.jpg", 800, 600, seed=1)
    first = ingest_image(path, data_config)
    _textured(path, 800, 600, seed=2)
    recorder = DecodeRecorder(monkeypatch)

    changed = ingest_image(path, data_config, known={first.id: first})

    assert changed.id != first.id
    assert not changed.carried_over
    assert recorder.decodes_of(path) == 1


# A stored record is not reused when its working copy has disappeared.
def test_a_missing_working_copy_forces_a_new_decode(tmp_path: Path, data_config: dict) -> None:
    path = _textured(tmp_path / "lost.jpg", 800, 600)
    first = ingest_image(path, data_config)
    working_copy_path(data_config, first.id).unlink()

    again = ingest_image(path, data_config, known={first.id: first})

    assert not again.carried_over
    assert working_copy_path(data_config, again.id).is_file()


# Byte-identical files give one photo (the first in path order); the copy is not decoded and is
# recorded as a separate record rejected as an exact duplicate.
def test_identical_files_collapse_to_one_photo(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "library"
    folder.mkdir()
    first = _textured(folder / "a.jpg", 800, 600, seed=1)
    twin = folder / "b_copy.jpg"
    twin.write_bytes(first.read_bytes())
    _textured(folder / "c.jpg", 800, 600, seed=2)
    recorder = DecodeRecorder(monkeypatch)

    photos = index_photos(folder, data_config, include_subfolders=False)

    assert [p.file_name for p in photos] == ["a.jpg", "b_copy.jpg", "c.jpg"]
    assert len({p.id for p in photos}) == 3
    assert [p.status == "rejected" for p in photos] == [False, True, False]
    assert photos[1].reject_reason == "duplicate_exact"
    assert photos[1].file_hash == photos[0].file_hash
    assert recorder.decodes_of(first) == 1
    assert recorder.decodes_of(twin) == 0


# A second indexing run over the same folder decodes nothing when the stored records are passed in.
def test_a_second_run_decodes_nothing(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "library"
    folder.mkdir()
    files = [_textured(folder / f"{i}.jpg", 700, 500, seed=i) for i in range(3)]
    stored = {p.id: p for p in index_photos(folder, data_config, include_subfolders=False)}
    recorder = DecodeRecorder(monkeypatch)

    again = index_photos(folder, data_config, include_subfolders=False, known=stored)

    assert [p.carried_over for p in again] == [True, True, True]
    assert all(recorder.decodes_of(f) == 0 for f in files)


# A record for a changed path replaces the one stored for the same path under another ID.
def test_a_changed_file_replaces_the_stale_record(tmp_path: Path, data_config: dict) -> None:
    path = _textured(tmp_path / "edit.jpg", 300, 200, seed=1)
    conn = db.connect(data_config)
    old = ingest_image(path, data_config)
    db.upsert_photo(conn, old)
    _textured(path, 300, 200, seed=2)
    new = ingest_image(path, data_config)
    db.upsert_photo(conn, new)

    rows = conn.execute("SELECT id FROM photos").fetchall()
    scores = conn.execute("SELECT photo_id FROM scores").fetchall()
    conn.close()

    assert [r["id"] for r in rows] == [new.id]
    assert [r["photo_id"] for r in scores] == [new.id]


# --- EXIF -----------------------------------------------------------------------------------


_ASCII, _SHORT, _LONG, _RATIONAL = 2, 3, 4, 5


# Encodes one little-endian TIFF directory starting at byte `base` of the TIFF block. Each entry is
# (tag, type, count, payload); payloads longer than four bytes are appended after the directory.
def _tiff_ifd(entries: list[tuple[int, int, int, bytes]], base: int) -> bytes:
    body = struct.pack("<H", len(entries))
    extra = b""
    data_start = base + 2 + 12 * len(entries) + 4
    for tag, kind, count, payload in sorted(entries):
        if len(payload) <= 4:
            value = payload.ljust(4, b"\0")
        else:
            value = struct.pack("<I", data_start + len(extra))
            extra += payload + (b"\0" if len(payload) % 2 else b"")
        body += struct.pack("<HHI", tag, kind, count) + value
    return body + struct.pack("<I", 0) + extra


# An ASCII entry for `_tiff_ifd`.
def _ascii(tag: int, text: str) -> tuple[int, int, int, bytes]:
    payload = text.encode("ascii") + b"\0"
    return tag, _ASCII, len(payload), payload


# A RATIONAL entry (degrees, minutes, seconds as whole numbers) for `_tiff_ifd`.
def _rational3(tag: int, values: tuple[int, int, int]) -> tuple[int, int, int, bytes]:
    return tag, _RATIONAL, 3, b"".join(struct.pack("<II", v, 1) for v in values)


# Builds the bytes of an EXIF block by hand (so the test does not depend on how Pillow writes
# nested directories): capture time, make, model, a GPS position and identifying extras.
def _rich_exif() -> bytes:
    exif_entries = [_ascii(0x9003, "2024:05:06 07:08:09"), _ascii(0xA431, "secret-serial")]
    gps_entries = [
        _ascii(1, "N"), _rational3(2, (48, 51, 24)), _ascii(3, "E"), _rational3(4, (2, 21, 7)),
    ]

    def main_entries(exif_offset: int, gps_offset: int) -> list[tuple[int, int, int, bytes]]:
        return [
            _ascii(0x010F, "SynthCam"),
            _ascii(0x0110, "Model One"),
            _ascii(0x013B, "secret-artist"),
            _ascii(0x0131, "secret-software"),
            _ascii(0x0132, "2023:01:01 00:00:00"),
            (ORIENTATION, _SHORT, 1, struct.pack("<H", 1)),
            (EXIF_IFD, _LONG, 1, struct.pack("<I", exif_offset)),
            (GPS_IFD, _LONG, 1, struct.pack("<I", gps_offset)),
        ]

    exif_offset = 8 + len(_tiff_ifd(main_entries(0, 0), 8))
    exif_block = _tiff_ifd(exif_entries, exif_offset)
    gps_offset = exif_offset + len(exif_block)
    main_block = _tiff_ifd(main_entries(exif_offset, gps_offset), 8)
    return b"Exif\0\0II*\0" + struct.pack("<I", 8) + main_block + exif_block + _tiff_ifd(gps_entries, gps_offset)


# Only the capture time, camera make and camera model are kept; GPS never reaches the record,
# the database, or the derived images.
def test_only_capture_time_make_and_model_are_kept(tmp_path: Path, data_config: dict) -> None:
    path = _textured(tmp_path / "tagged.jpg", 400, 300, exif=_rich_exif())
    with Image.open(path) as source:  # the fixture really carries a GPS position
        assert source.getexif().get_ifd(GPS_IFD)

    photo = ingest_image(path, data_config)
    conn = db.connect(data_config)
    db.upsert_photo(conn, photo)
    db.write_mirror(conn, data_config)
    stored = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
    conn.close()

    assert photo.exif == {
        "DateTimeOriginal": "2024:05:06 07:08:09",
        "Make": "SynthCam",
        "Model": "Model One",
    }
    assert json.loads(stored[0]["exif"]) == photo.exif
    everything = json.dumps(stored, default=str) + (Path(data_config["output"]["working_dir"]) / "photos.json").read_text(
        encoding="utf-8"
    )
    for leaked in ("GPS", "secret-artist", "secret-software", "secret-serial", "Orientation"):
        assert leaked not in everything
    for derived in (working_copy_path(data_config, photo.id), Path(photo.thumbnail_path)):
        with Image.open(derived) as image:
            assert len(image.getexif()) == 0
            assert not ({"exif", "icc_profile", "comment"} & set(image.info))


# An image without EXIF, or with a broken block, still ingests with an empty subset.
def test_missing_exif_gives_an_empty_subset(tmp_path: Path, data_config: dict) -> None:
    photo = ingest_image(_textured(tmp_path / "plain.jpg", 300, 200), data_config)
    assert photo.exif == {}
    assert photo.status != "rejected"


# --- originals are read-only ----------------------------------------------------------------


# Ingest only ever opens the original for reading, and leaves its bytes and times untouched.
def test_the_original_is_opened_read_only(
    tmp_path: Path, data_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _textured(tmp_path / "keep.jpg", 1600, 1200, exif=_rich_exif())
    before_bytes = path.read_bytes()
    before_stat = path.stat()
    target = path.resolve()
    modes: list[str] = []
    real_open = builtins.open

    def open_recorded(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, (str, Path)) and Path(file).resolve() == target:
            modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_recorded)

    photo = ingest_image(path, data_config)

    monkeypatch.undo()
    assert photo.status != "rejected"
    assert modes and set(modes) == {"rb"}
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert path.stat().st_size == before_stat.st_size


# A readable file that is not an image is rejected as unreadable but still has its content ID,
# and no working copy is left behind.
def test_a_corrupt_file_is_rejected_but_keeps_a_content_id(tmp_path: Path, data_config: dict) -> None:
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image at all")

    photo = ingest_image(path, data_config)

    assert photo.status == "rejected" and photo.reject_reason == "unreadable_image"
    assert photo.id == hashlib.sha256(b"not an image at all").hexdigest()
    assert not working_copy_path(data_config, photo.id).exists()


# Carried-over state is transient: it is never serialized or persisted.
def test_carried_over_is_not_persisted(data_config: dict) -> None:
    photo = Photo(id="a" * 64, file_path="x.jpg", file_name="x.jpg", carried_over=True)
    conn = db.connect(data_config)
    db.upsert_photo(conn, photo)
    loaded = db.load_photos(conn)
    conn.close()
    assert "carriedOver" not in photo.to_camel()
    assert [p.carried_over for p in loaded] == [False]
