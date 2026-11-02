"""Tests for the Q-ReAlign and SigLIP 2 stages, the safetensors-only model loader and the no-download rules.

Models are fakes: no torch, no weights and no network. Every image is synthetic and lives under pytest's tmp_path;
no personal photo is read.
"""

import ast
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pytest
from PIL import Image

import main as engine_main
from src import db, gpu_models, neural_stages
from src.gpu_models import AESTHETIC, QUALITY, load_qrealign, load_siglip2, resolve_device, weighted_level_value
from src.ingest import ingest_image
from src.model_loader import (
    MODELS_DIR_ENV_VAR,
    ModelUnavailable,
    locate_model,
    model_dir_name,
    verify_model_dir,
)
from src.neural_stages import StageReport, batch_size, embed_siglip2, score_qrealign
from src.utils import Photo, working_copy_path

ENGINE_DIR = Path(__file__).resolve().parents[1]


# Writes a file whose first bytes form a valid safetensors header (an empty tensor table).
def _write_safetensors(path: Path) -> Path:
    header = b'{"__metadata__":{}}'
    path.write_bytes(len(header).to_bytes(8, "little") + header)
    return path


# A configuration whose models live in an empty folder under tmp_path, with the environment override removed.
@pytest.fixture()
def model_config(data_config: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.delenv(MODELS_DIR_ENV_VAR, raising=False)
    models = tmp_path / "models"
    models.mkdir()
    data_config["models"]["dir"] = str(models)
    return data_config


# Ingests `count` distinct synthetic photos (working copies included) and stores them.
def _photos(tmp_path: Path, config: dict, conn: Any, count: int) -> list[Photo]:
    folder = tmp_path / "originals"
    folder.mkdir(exist_ok=True)
    photos: list[Photo] = []
    for seed in range(count):
        pixels = np.random.default_rng(seed).integers(0, 256, size=(480, 640, 3), dtype=np.uint8)
        path = folder / f"p{seed}.jpg"
        Image.fromarray(pixels).save(path, "JPEG", quality=90)
        photo = ingest_image(path, config)
        db.upsert_photo(conn, photo)
        photos.append(photo)
    return photos


# Returns the mean brightness (0 to 1) of a photo's working copy, the value the fake models derive from it.
def _brightness(config: dict, photo: Photo) -> float:
    with Image.open(working_copy_path(config, photo.id)) as image:
        return float(np.asarray(image.convert("RGB")).mean()) / 255.0


class FakeScorer:
    """Records every call; quality is the image brightness and aesthetics its complement."""

    name = "fake-qrealign"

    def __init__(self, device: str = "cuda", bad: Any = None) -> None:
        self.device = device
        self.calls: list[tuple[int, str]] = []
        self._bad = bad

    def score(self, images: Sequence[Image.Image], task: str) -> list[float]:
        self.calls.append((len(images), task))
        if self._bad is not None:
            return self._bad(images, task)
        values = [float(np.asarray(image).mean()) / 255.0 for image in images]
        return values if task == QUALITY else [1.0 - value for value in values]


class FakeEmbedder:
    """Records every call; returns a deterministic, deliberately unnormalized 8-dimensional vector per image."""

    name = "fake-siglip2"

    def __init__(self, device: str = "cuda", bad: Any = None) -> None:
        self.device = device
        self.calls: list[int] = []
        self._bad = bad

    def embed(self, images: Sequence[Image.Image]) -> np.ndarray:
        self.calls.append(len(images))
        if self._bad is not None:
            return self._bad(images)
        rows = [np.asarray(image).reshape(-1, 3).mean(axis=0).tolist() + [1.0] * 5 for image in images]
        return np.array(rows, dtype=np.float32) * 7.0


# --- Q-ReAlign: batching and stored values -----------------------------------------------------------------


# Photos reach the model in batches of the configured size, once per task, and the stored values are the model's.
def test_qrealign_runs_in_batches_and_stores_values_between_zero_and_one(
    tmp_path: Path, model_config: dict, capsys: pytest.CaptureFixture
) -> None:
    model_config["models"]["qrealign"]["batch_size"] = 2
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 5)
    scorer = FakeScorer()
    report = score_qrealign(photos, model_config, conn, scorer=scorer)
    assert scorer.calls == [
        (2, QUALITY), (2, AESTHETIC), (2, QUALITY), (2, AESTHETIC), (1, QUALITY), (1, AESTHETIC),
    ]
    assert report == StageReport(stored=5, unknown=0)
    stored = db.load_neural_scores(conn)
    assert set(stored) == {p.id for p in photos}
    for photo in photos:
        row = stored[photo.id]
        assert row["model"] == "fake-qrealign"
        assert 0.0 <= row["quality"] <= 1.0 and 0.0 <= row["aesthetic"] <= 1.0
        assert row["quality"] == pytest.approx(_brightness(model_config, photo), abs=1e-6)
        assert row["aesthetic"] == pytest.approx(1.0 - _brightness(model_config, photo), abs=1e-6)
    assert "on cuda" in capsys.readouterr().out
    conn.close()


# The batch size comes from config and is clamped; a bad value falls back to the default.
def test_batch_size_is_clamped() -> None:
    assert batch_size({"models": {"qrealign": {"batch_size": 8}}}, "qrealign") == 8
    assert batch_size({"models": {"qrealign": {"batch_size": 0}}}, "qrealign") == 1
    assert batch_size({"models": {"qrealign": {"batch_size": 10**6}}}, "qrealign") == neural_stages.MAX_BATCH_SIZE
    assert batch_size({"models": {"qrealign": {"batch_size": "many"}}}, "qrealign") == neural_stages.DEFAULT_BATCH_SIZE
    assert batch_size({}, "siglip2") == neural_stages.DEFAULT_BATCH_SIZE


# Rejected photos never reach a model and get no row.
def test_rejected_photos_are_skipped(tmp_path: Path, model_config: dict) -> None:
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 2)
    photos[0].status = "rejected"
    scorer = FakeScorer()
    score_qrealign(photos, model_config, conn, scorer=scorer)
    assert scorer.calls == [(1, QUALITY), (1, AESTHETIC)]
    assert set(db.load_neural_scores(conn)) == {photos[1].id}
    conn.close()


# An answer outside 0 to 1, a non-number or a NaN is stored as unknown, never clipped into range or replaced.
def test_unusable_answers_are_unknown(tmp_path: Path, model_config: dict) -> None:
    model_config["models"]["qrealign"]["batch_size"] = 4
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 4)
    scorer = FakeScorer(bad=lambda images, task: [1.5, math.nan, "x", 0.25] if task == QUALITY else [0.5] * 4)
    report = score_qrealign(photos, model_config, conn, scorer=scorer)
    stored = db.load_neural_scores(conn)
    assert [stored[p.id]["quality"] for p in photos] == [None, None, None, 0.25]
    assert [stored[p.id]["aesthetic"] for p in photos] == [0.5] * 4
    assert report == StageReport(stored=4, unknown=0)
    conn.close()


# A batch that raises (for example out of GPU memory) or returns the wrong count is unknown; the next batch runs.
def test_a_failed_batch_is_unknown_and_the_run_continues(tmp_path: Path, model_config: dict) -> None:
    model_config["models"]["qrealign"]["batch_size"] = 2
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 4)
    state = {"batch": 0}

    def bad(images: Sequence[Image.Image], task: str) -> list[float]:
        if task == QUALITY:
            state["batch"] += 1
        if state["batch"] == 1:
            raise RuntimeError("out of memory")
        return [0.5] * (len(images) - 1) if task == AESTHETIC else [0.5] * len(images)

    report = score_qrealign(photos, model_config, conn, scorer=FakeScorer(bad=bad))
    stored = db.load_neural_scores(conn)
    assert [stored[p.id]["quality"] for p in photos] == [None, None, 0.5, 0.5]
    assert [stored[p.id]["aesthetic"] for p in photos] == [None] * 4
    assert report == StageReport(stored=2, unknown=2)
    conn.close()


# A photo without a readable working copy is unknown and never reaches the model.
def test_a_missing_working_copy_is_unknown(tmp_path: Path, model_config: dict) -> None:
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 2)
    working_copy_path(model_config, photos[0].id).unlink()
    scorer = FakeScorer()
    report = score_qrealign(photos, model_config, conn, scorer=scorer)
    stored = db.load_neural_scores(conn)
    assert scorer.calls[0][0] == 1
    assert stored[photos[0].id]["quality"] is None and stored[photos[0].id]["aesthetic"] is None
    assert stored[photos[1].id]["quality"] is not None
    assert report == StageReport(stored=1, unknown=1)
    conn.close()


# --- a missing model gives a clear error and unknown values ------------------------------------------------


# With no model directory the stage reports a structured error, stores unknown values and invents nothing.
def test_missing_qrealign_model_gives_an_error_and_unknown_values(
    tmp_path: Path, model_config: dict, capsys: pytest.CaptureFixture
) -> None:
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 3)
    report = score_qrealign(photos, model_config, conn)
    assert report.stored == 0 and report.unknown == 3
    assert isinstance(report.error, ModelUnavailable) and report.error.code == "directory_missing"
    stored = db.load_neural_scores(conn)
    assert all(stored[p.id]["quality"] is None and stored[p.id]["aesthetic"] is None for p in photos)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    errors = [e for e in events if e.get("level") == "error"]
    assert errors and errors[0]["code"] == "model_directory_missing" and errors[0]["model"] == "qrealign"
    assert "q-realign-mini" in errors[0]["message"] and str(tmp_path) not in errors[0]["message"]
    conn.close()


# With no model directory no embedding is stored, so there is no substitute vector to group on.
def test_missing_siglip2_model_gives_an_error_and_no_embeddings(tmp_path: Path, model_config: dict) -> None:
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 2)
    report = embed_siglip2(photos, model_config, conn)
    assert report.stored == 0 and report.unknown == 2
    assert report.error is not None and report.error.code == "directory_missing"
    assert db.load_embeddings(conn) == {}
    conn.close()


# A loader that raises ModelUnavailable is handled the same way as a missing directory.
def test_a_loader_error_leaves_values_unknown(tmp_path: Path, model_config: dict) -> None:
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 1)

    def loader(config: dict) -> Any:
        raise ModelUnavailable("qrealign", "cuda_unavailable", "no CUDA device")

    report = score_qrealign(photos, model_config, conn, loader=loader)
    assert report.error is not None and report.error.code == "cuda_unavailable"
    assert db.load_neural_scores(conn)[photos[0].id]["quality"] is None
    conn.close()


# --- SigLIP 2: batching and stored vectors -----------------------------------------------------------------


# Photos reach SigLIP 2 in batches of the configured size; each stored vector has unit length.
def test_siglip2_runs_in_batches_and_stores_unit_vectors(tmp_path: Path, model_config: dict) -> None:
    model_config["models"]["siglip2"]["batch_size"] = 3
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 7)
    embedder = FakeEmbedder()
    report = embed_siglip2(photos, model_config, conn, embedder=embedder)
    assert embedder.calls == [3, 3, 1]
    assert report == StageReport(stored=7, unknown=0)
    vectors = db.load_embeddings(conn)
    assert set(vectors) == {p.id for p in photos}
    for vector in vectors.values():
        assert vector.dtype == np.float32 and vector.shape == (8,)
        assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)
    row = conn.execute("SELECT model, dim FROM embeddings LIMIT 1").fetchone()
    assert (row["model"], row["dim"]) == ("fake-siglip2", 8)
    conn.close()


# A zero, non-finite or wrongly shaped answer gives no embedding for that photo, and no other value is made up.
def test_unusable_embeddings_are_unknown(tmp_path: Path, model_config: dict) -> None:
    model_config["models"]["siglip2"]["batch_size"] = 4
    conn = db.connect(model_config)
    photos = _photos(tmp_path, model_config, conn, 4)

    def bad(images: Sequence[Image.Image]) -> np.ndarray:
        rows = np.ones((len(images), 4), dtype=np.float32)
        rows[0] = 0.0
        rows[1, 2] = np.nan
        return rows

    report = embed_siglip2(photos, model_config, conn, embedder=FakeEmbedder(bad=bad))
    assert set(db.load_embeddings(conn)) == {photos[2].id, photos[3].id}
    assert report == StageReport(stored=2, unknown=2)

    other = db.connect(model_config)
    other.execute("DELETE FROM embeddings")
    other.commit()
    wrong_shape = FakeEmbedder(bad=lambda images: np.ones((len(images) + 1, 4), dtype=np.float32))
    assert embed_siglip2(photos, model_config, other, embedder=wrong_shape).stored == 0
    raising = FakeEmbedder(bad=lambda images: (_ for _ in ()).throw(RuntimeError("out of memory")))
    assert embed_siglip2(photos, model_config, other, embedder=raising).stored == 0
    assert db.load_embeddings(other) == {}
    other.close()
    conn.close()


# --- device and score arithmetic ---------------------------------------------------------------------------


class _Torch:
    """The part of torch that resolve_device reads."""

    def __init__(self, cuda: bool) -> None:
        self.cuda = type("Cuda", (), {"is_available": staticmethod(lambda: cuda)})


# auto takes the GPU when there is one; an explicit cuda without a GPU is an error, never a silent CPU run.
def test_device_resolution() -> None:
    assert resolve_device("auto", _Torch(True)) == "cuda"
    assert resolve_device("auto", _Torch(False)) == "cpu"
    assert resolve_device("cuda", _Torch(True)) == "cuda"
    assert resolve_device("cpu", _Torch(True)) == "cpu"
    with pytest.raises(ModelUnavailable) as caught:
        resolve_device("cuda", _Torch(False), "qrealign")
    assert caught.value.code == "cuda_unavailable"
    with pytest.raises(ModelUnavailable):
        resolve_device("tpu", _Torch(True))


# The value is the probability-weighted level, from 0 (all bad) to 1 (all excellent).
def test_weighted_level_value() -> None:
    assert weighted_level_value([1, 0, 0, 0, 0]) == 1.0
    assert weighted_level_value([0, 0, 0, 0, 1]) == 0.0
    assert weighted_level_value([0.2] * 5) == pytest.approx(0.5)
    assert weighted_level_value([2, 0, 0, 0, 2]) == pytest.approx(0.5)  # renormalized
    for bad in ([1, 0, 0], [0, 0, 0, 0, 0], [-1, 1, 1, 1, 1], [math.nan, 1, 1, 1, 1]):
        with pytest.raises(ValueError):
            weighted_level_value(bad)


# The database refuses an out-of-range value instead of keeping it.
def test_the_database_refuses_out_of_range_scores(model_config: dict) -> None:
    conn = db.connect(model_config)
    db.upsert_photo(conn, Photo(id="a" * 64, file_path="x.jpg", file_name="x.jpg"))
    for bad in (1.5, -0.1, math.nan):
        with pytest.raises(ValueError):
            db.save_neural_scores(conn, "a" * 64, "m", bad, 0.5)
    db.save_neural_scores(conn, "a" * 64, "m", 0.0, 1.0)
    assert db.load_neural_scores(conn)["a" * 64] == {"model": "m", "quality": 0.0, "aesthetic": 1.0}
    conn.close()


# --- safetensors-only loading ------------------------------------------------------------------------------


# A directory with safetensors weights is accepted, sharded or not; other files beside them are ignored.
def test_safetensors_weights_are_accepted(tmp_path: Path) -> None:
    directory = tmp_path / "m"
    directory.mkdir()
    _write_safetensors(directory / "model-00001-of-00002.safetensors")
    _write_safetensors(directory / "model-00002-of-00002.safetensors")
    (directory / "config.json").write_text("{}")
    assert [w.name for w in verify_model_dir(directory, "qrealign")] == [
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    ]


# A pickle-only directory is refused as unsafe, whichever pickle-based suffix it uses.
@pytest.mark.parametrize("name", ["w.bin", "w.pt", "w.pth", "w.pkl", "w.ckpt", "w.pickle", "W.BIN"])
def test_pickle_weights_are_refused(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"\x80\x04pickle")
    with pytest.raises(ModelUnavailable) as caught:
        verify_model_dir(tmp_path, "qrealign")
    assert caught.value.code == "unsafe_format"
    assert "safetensors" in caught.value.message


# Pickle files beside safetensors weights are never loaded: the loaders ask for safetensors only.
def test_pickle_beside_safetensors_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "pytorch_model.bin").write_bytes(b"\x80\x04pickle")
    _write_safetensors(tmp_path / "model.safetensors")
    assert [w.name for w in verify_model_dir(tmp_path, "siglip2")] == ["model.safetensors"]


# A .safetensors file that is really something else is refused by its first bytes; an empty directory has no weights.
def test_invalid_and_missing_weights(tmp_path: Path) -> None:
    (tmp_path / "model.safetensors").write_bytes(b"\x80\x04 this is a pickle renamed")
    with pytest.raises(ModelUnavailable) as invalid:
        verify_model_dir(tmp_path, "qrealign")
    assert invalid.value.code == "invalid_weights"
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ModelUnavailable) as none:
        verify_model_dir(empty, "qrealign")
    assert none.value.code == "no_weights"
    with pytest.raises(ModelUnavailable) as absent:
        verify_model_dir(tmp_path / "absent", "qrealign")
    assert absent.value.code == "directory_missing"


# The model name must be one plain directory name: a hub identifier, a path or a parent reference never loads.
@pytest.mark.parametrize(
    "value", ["owner/name", "../models", "/abs/path", "a/../b", "", "..", ".hidden", "https://x/y", None, 5]
)
def test_model_directory_names_cannot_be_hub_ids_or_paths(model_config: dict, value: Any) -> None:
    model_config["models"]["qrealign"]["dir"] = value
    with pytest.raises(ModelUnavailable) as caught:
        model_dir_name(model_config, "qrealign")
    assert caught.value.code == "not_configured"
    with pytest.raises(ModelUnavailable):
        locate_model(model_config, "qrealign")


# The models volume comes from the environment in the api container and from the configuration elsewhere.
def test_models_root_prefers_the_environment(
    model_config: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "volume"
    (volume / "q-realign-mini").mkdir(parents=True)
    _write_safetensors(volume / "q-realign-mini" / "model.safetensors")
    with pytest.raises(ModelUnavailable):
        locate_model(model_config, "qrealign")
    monkeypatch.setenv(MODELS_DIR_ENV_VAR, str(volume))
    assert locate_model(model_config, "qrealign") == volume / "q-realign-mini"


# With valid weights and no torch stack (the engine check), the loaders say what is missing and load nothing.
# A None entry in sys.modules makes `import torch` raise ImportError, so the test runs the same way whether or not
# the GPU requirements are installed.
@pytest.mark.parametrize("load, key, directory", [
    (load_qrealign, "qrealign", "q-realign-mini"),
    (load_siglip2, "siglip2", "siglip2-base-patch16-224"),
])
def test_loaders_report_a_missing_stack(
    model_config: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, load: Any, key: str, directory: str
) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)
    (tmp_path / "models" / directory).mkdir()
    _write_safetensors(tmp_path / "models" / directory / "model.safetensors")
    with pytest.raises(ModelUnavailable) as caught:
        load(model_config)
    assert caught.value.code == "dependency_missing" and caught.value.model == key
    assert "requirements-gpu.txt" in caught.value.message


# --- no code path downloads a model ------------------------------------------------------------------------

_MODEL_MODULES = ("gpu_models.py", "model_loader.py", "neural_stages.py")
_NETWORK_MODULES = {"requests", "httpx", "urllib", "http", "socket", "huggingface_hub", "aiohttp", "ftplib"}
_DOWNLOAD_CALLS = {"snapshot_download", "hf_hub_download", "urlopen", "urlretrieve", "cached_download"}


# Returns the parsed syntax tree of an engine source file.
def _tree(relative: str) -> ast.AST:
    return ast.parse((ENGINE_DIR / "src" / relative).read_text(encoding="utf-8"))


# The model modules import no network library and call no download function.
@pytest.mark.parametrize("module", _MODEL_MODULES)
def test_model_modules_have_no_network_or_download_calls(module: str) -> None:
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            assert not {alias.name.split(".")[0] for alias in node.names} & _NETWORK_MODULES
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in _NETWORK_MODULES
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert called not in _DOWNLOAD_CALLS


# Every from_pretrained call reads a local directory only: not a literal name, with local_files_only and no remote
# code; model classes also demand safetensors.
def test_every_from_pretrained_call_is_local_and_safe() -> None:
    calls = [
        node
        for node in ast.walk(_tree("gpu_models.py"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "from_pretrained"
    ]
    assert len(calls) == 4  # a processor and a model for each of the two networks
    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert not isinstance(call.args[0], ast.Constant), "a literal name would be a hub identifier"
        assert isinstance(keywords["local_files_only"], ast.Constant) and keywords["local_files_only"].value is True
        assert isinstance(keywords["trust_remote_code"], ast.Constant) and keywords["trust_remote_code"].value is False
    model_calls = [c for c in calls if "Processor" not in ast.unparse(c.func)]
    assert len(model_calls) == 2
    for call in model_calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert isinstance(keywords["use_safetensors"], ast.Constant) and keywords["use_safetensors"].value is True


# The model stages call no loader that could fetch weights, and the loaders are the only way they get a model.
def test_stages_are_wired_through_the_local_loaders() -> None:
    assert engine_main.score_qrealign is score_qrealign
    assert engine_main.embed_siglip2 is embed_siglip2
    assert neural_stages.load_qrealign is gpu_models.load_qrealign
    assert neural_stages.load_siglip2 is gpu_models.load_siglip2


# --- the engine check stays fast ---------------------------------------------------------------------------

_HEAVY = {"torch", "torchvision", "transformers", "safetensors", "open-clip-torch", "open_clip_torch", "timm"}


# Returns the package names of a requirements file, ignoring comments and blank lines.
def _requirement_names(name: str) -> set[str]:
    lines = (ENGINE_DIR / name).read_text(encoding="utf-8").splitlines()
    return {line.split("==")[0].strip().lower() for line in lines if line.strip() and not line.startswith("#")}


# The engine check installs requirements.txt, so no heavy package may be in it; the GPU file holds them, pinned.
def test_heavy_dependencies_live_in_the_gpu_requirements_file() -> None:
    assert not _requirement_names("requirements.txt") & _HEAVY
    assert {"torch", "transformers", "safetensors"} <= _requirement_names("requirements-gpu.txt")
    pins = [
        line for line in (ENGINE_DIR / "requirements-gpu.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert all("==" in line for line in pins)
