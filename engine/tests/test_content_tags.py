"""Content tags: the fixed list, zero-shot matching with fake text encoders, storage and graceful degradation."""

import re
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pytest

from src import content_tags, db
from src.content_tags import CONTENT_TAGS, tag_content
from src.model_loader import ModelUnavailable
from src.utils import Photo

EXPECTED = [
    "portrait", "group", "landscape", "cityscape", "architecture", "nature", "wildlife", "pet",
    "food", "event", "sport", "macro", "night", "abstract", "document", "other",
]  # fmt: skip
DIM = len(EXPECTED)


class FakeTextEncoder:
    """Label i gets the i-th unit axis (scaled, unnormalized), so a photo vector near axis i is tagged label i."""

    name = "fake-siglip2"
    device = "cpu"

    def __init__(self, bad: Any = None) -> None:
        self.texts: list[str] = []
        self._bad = bad

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        self.texts = list(texts)
        if self._bad is not None:
            return self._bad(texts)
        return np.eye(len(texts), DIM, dtype=np.float32) * 3.0


class NoTextTower:
    name = "image-only"
    device = "cpu"


# Stores photos and one embedding per photo, each close to the axis of the given label index.
def _setup(data_config: dict, axes: list[int]) -> tuple[Any, list[Photo]]:
    conn = db.connect(data_config)
    photos = []
    for index, axis in enumerate(axes):
        photo = Photo(id=f"photo-{index:02d}", file_path=f"/x/{index}.jpg", file_name=f"{index}.jpg")
        db.upsert_photo(conn, photo)
        vector = np.full(DIM, 0.01, dtype=np.float32)
        vector[axis] = 1.0
        db.save_embedding(conn, photo.id, vector / np.linalg.norm(vector), "fake-siglip2")
        photos.append(photo)
    return conn, photos


def test_the_constant_is_the_fixed_list_of_the_pipeline_document() -> None:
    assert list(CONTENT_TAGS) == EXPECTED
    document = Path(__file__).resolve().parents[2] / "docs" / "pipeline.md"
    if document.exists():  # the engine container mounts engine/ only, so the document check runs on the host
        text = document.read_text(encoding="utf-8")
        section = text.split("### 4. Embedding and content tags", 1)[1].split("###", 1)[0]
        assert re.findall(r"`([a-z]+)`", section.split("Implemented in", 1)[0]) == EXPECTED


def test_tags_follow_the_closest_label_and_are_in_the_list(data_config: dict) -> None:
    conn, photos = _setup(data_config, [0, 5, 14, 15])
    encoder = FakeTextEncoder()
    report = tag_content(photos, data_config, conn, encoder=encoder)
    tags = db.load_content_tags(conn)
    assert (report.stored, report.unknown) == (4, 0)
    assert [tags[p.id]["tag"] for p in photos] == ["portrait", "nature", "document", "other"]
    assert all(row["tag"] in CONTENT_TAGS and row["model"] == "fake-siglip2" for row in tags.values())
    assert len(encoder.texts) == len(CONTENT_TAGS) and "document" in encoder.texts[14]


def test_tags_are_replaced_on_a_rerun(data_config: dict) -> None:
    conn, photos = _setup(data_config, [0])
    tag_content(photos, data_config, conn, encoder=FakeTextEncoder())
    vector = np.zeros(DIM, dtype=np.float32)
    vector[3] = 1.0
    db.save_embedding(conn, photos[0].id, vector, "fake-siglip2")
    tag_content(photos, data_config, conn, encoder=FakeTextEncoder())
    rows = db.load_content_tags(conn)
    assert len(rows) == 1 and rows[photos[0].id]["tag"] == "cityscape"


def test_rejected_and_unembedded_photos_get_no_tag(data_config: dict) -> None:
    conn, photos = _setup(data_config, [0, 1])
    photos[0].status = "rejected"
    stray = Photo(id="photo-99", file_path="/x/99.jpg", file_name="99.jpg")
    db.upsert_photo(conn, stray)
    report = tag_content([*photos, stray], data_config, conn, encoder=FakeTextEncoder())
    assert set(db.load_content_tags(conn)) == {photos[1].id}
    assert (report.stored, report.unknown) == (1, 1)  # the rejected photo is not eligible; only the stray is unknown


def test_a_model_without_a_text_tower_gives_no_tags(data_config: dict, capsys: pytest.CaptureFixture) -> None:
    conn, photos = _setup(data_config, [0])
    report = tag_content(photos, data_config, conn, encoder=NoTextTower())  # type: ignore[arg-type]
    assert (report.stored, report.unknown) == (0, 1)
    assert db.load_content_tags(conn) == {}
    assert "no text tower" in capsys.readouterr().out


def test_a_missing_model_gives_a_structured_error_and_no_tags(data_config: dict, capsys: pytest.CaptureFixture) -> None:
    conn, photos = _setup(data_config, [0])

    def loader(config: dict) -> Any:
        raise ModelUnavailable("siglip2", "not_found", "put the model in the volume")

    report = tag_content(photos, data_config, conn, loader=loader)
    assert report.error is not None and report.stored == 0
    assert db.load_content_tags(conn) == {}
    assert '"code": "model_not_found"' in capsys.readouterr().out


@pytest.mark.parametrize(
    "bad",
    [
        lambda texts: np.ones((3, DIM), dtype=np.float32),  # wrong row count
        lambda texts: np.ones(DIM, dtype=np.float32),  # wrong rank
        lambda texts: np.full((len(texts), DIM), np.nan, dtype=np.float32),  # not finite
        lambda texts: np.zeros((len(texts), DIM), dtype=np.float32),  # no length
        lambda texts: (_ for _ in ()).throw(RuntimeError("out of memory")),
        lambda texts: np.eye(len(texts), DIM + 4, dtype=np.float32),  # dimension differs from the image vectors
    ],
)
def test_unusable_text_vectors_give_no_tag(data_config: dict, bad: Any) -> None:
    conn, photos = _setup(data_config, [0, 1])
    report = tag_content(photos, data_config, conn, encoder=FakeTextEncoder(bad=bad))
    assert report.stored == 0
    assert db.load_content_tags(conn) == {}


def test_unusable_image_vectors_give_no_tag(data_config: dict) -> None:
    conn, photos = _setup(data_config, [0, 1])
    db.save_embedding(conn, photos[0].id, np.full(DIM, np.nan, dtype=np.float32), "fake-siglip2")
    db.save_embedding(conn, photos[1].id, np.ones(DIM + 2, dtype=np.float32), "fake-siglip2")
    report = tag_content(photos, data_config, conn, encoder=FakeTextEncoder())
    assert (report.stored, report.unknown) == (0, 2)
    assert db.load_content_tags(conn) == {}


def test_the_analyze_pipeline_calls_the_tag_stage() -> None:
    import main as engine_main

    assert engine_main.tag_content is content_tags.tag_content
