"""Tests of the pinned model fetcher with local fake files: no network, and no real model is downloaded."""

import hashlib
import io
import json
import pickle
from pathlib import Path

import pytest

from src.model_fetch import fetch as mf

# The pins supplied by the owner on 2026-11-02, copied here so the manifest is checked against an outside source.
OWNER_PINS = {
    "qwen3.8-27b-iq2_xs": [
        ("ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF", "d562806dbafae37109975e970aae91b43e73b440",
         "Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf", "f0ae5006da0ce6225935339e4e989369f94de95d2263cf969519f8420c9ae02c",
         8422841472),
        ("ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF", "d562806dbafae37109975e970aae91b43e73b440",
         "mmproj-Qwen3.8-27B-BF16.gguf", "13cb7bebccbd04afc8f4090cb949ecf8937cdf7377c5799b1a0c594e7c0d3e16",
         931146528),
    ],
    "qwen3.8-27b-iq2_s": [
        ("ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF", "d562806dbafae37109975e970aae91b43e73b440",
         "Qwen3.8-27B-GSQ-RCO-IQ2_S.gguf", "16c9802111aa9ef3acde465188d6d601f8db128ee3d828ad983a5caca4135ecb",
         9259510912),
        ("ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF", "d562806dbafae37109975e970aae91b43e73b440",
         "mmproj-Qwen3.8-27B-BF16.gguf", "13cb7bebccbd04afc8f4090cb949ecf8937cdf7377c5799b1a0c594e7c0d3e16",
         931146528),
    ],
    "qwen3.5-9b-q5_k_m": [
        ("unsloth/Qwen3.5-9B-GGUF", "3885219b6810b007914f3a7950a8d1b469d598a5",
         "Qwen3.5-9B-Q5_K_M.gguf", "dc2a39aef291f91a9116ad214058da0d86eb648743a124bd8c333787c4b9c91c",
         6577841376),
        ("unsloth/Qwen3.5-9B-GGUF", "3885219b6810b007914f3a7950a8d1b469d598a5",
         "mmproj-BF16.gguf", "853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383",
         921705024),
    ],
    "gemma-4-12b-q4_k_m": [
        ("unsloth/gemma-4-12b-it-GGUF", "fc034cfff751157913579611efad8462ac1be606",
         "gemma-4-12b-it-Q4_K_M.gguf", "0a270ec9fe6b34f4a0d33992b6135117b484ebc4766ab76b51d4ae8c457e4c42",
         7121861440),
        ("unsloth/gemma-4-12b-it-GGUF", "fc034cfff751157913579611efad8462ac1be606",
         "mmproj-BF16.gguf", "2e269f906eb15169ee9ce880ea649bd6d42d4964c21f8ede10d0d0efc738bcbb",
         175115840),
    ],
}

FAKE_GGUF = mf.GGUF_MAGIC + b"\x03\x00\x00\x00" + bytes(range(256)) * 40


# Returns a pinned file for local fake content, with a target of the given name.
def _pin(content: bytes, target: str = "fake-model.gguf", sha256: str | None = None) -> mf.PinnedFile:
    return mf.PinnedFile(
        repo="example/fake-GGUF",
        revision="0" * 40,
        file=target,
        target=target,
        sha256=sha256 or hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


# Returns an opener that serves a local fake file and records each requested URL.
def _local_opener(source: Path, calls: list[str]):
    def open_url(url: str, timeout: float):
        calls.append(url)
        return open(source, "rb")

    return open_url


# Returns an opener that fails the test if a download is attempted.
def _no_download(url: str, timeout: float):
    raise AssertionError(f"no download was expected, got {url}")


# Writes fake content to a local source file outside the models directory and returns its path.
def _source(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "source.bin"
    path.write_bytes(content)
    return path


# Returns an empty stand-in for the models volume, apart from the fake source files.
@pytest.fixture()
def models_dir(tmp_path: Path) -> Path:
    path = tmp_path / "models"
    path.mkdir()
    return path


def test_the_manifest_pins_exactly_the_owner_supplied_files():
    manifest = mf.load_manifest()
    assert manifest.default == "qwen3.8-27b-iq2_xs"
    actual = {
        model.id: [(f.repo, f.revision, f.file, f.sha256, f.size_bytes) for f in model.files]
        for model in manifest.models
    }
    assert actual == OWNER_PINS


def test_every_manifest_file_has_a_revision_a_sha256_and_an_accepted_format():
    manifest = mf.load_manifest()
    targets = set()
    for model in manifest.models:
        roles = [entry["role"] for entry in _raw_model(model.id)["files"]]
        assert roles == ["weights", "mmproj"], model.id
        for pinned in model.files:
            assert len(pinned.revision) == 40 and int(pinned.revision, 16) >= 0
            assert len(pinned.sha256) == 64 and int(pinned.sha256, 16) >= 0
            assert pinned.target.endswith(".gguf") and pinned.file.endswith(".gguf")
            assert pinned.url == f"https://huggingface.co/{pinned.repo}/resolve/{pinned.revision}/{pinned.file}"
            targets.add((pinned.target, pinned.sha256))
    # The two upstream files named mmproj-BF16.gguf differ, so each gets its own target in the volume.
    names = [target for target, _ in targets]
    assert len(names) == len(set(names))


# Returns the raw manifest entry of a model, to read fields the dataclass does not keep.
def _raw_model(model_id: str) -> dict:
    data = json.loads(mf.MANIFEST_PATH.read_text(encoding="utf-8"))
    return next(model for model in data["models"] if model["id"] == model_id)


def test_selection_defaults_to_the_default_model_and_never_repeats_a_target():
    manifest = mf.load_manifest()
    default = mf.select_files(manifest, [])
    assert [f.target for f in default] == ["Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf", "mmproj-Qwen3.8-27B-BF16.gguf"]
    both = mf.select_files(manifest, ["qwen3.8-27b-iq2_xs", "qwen3.8-27b-iq2_s"])
    assert [f.target for f in both].count("mmproj-Qwen3.8-27B-BF16.gguf") == 1
    assert len(mf.select_files(manifest, ["all"])) == 7
    with pytest.raises(mf.ManifestError, match="unknown model"):
        mf.select_files(manifest, ["not-a-model"])


def test_a_matching_file_is_accepted_and_renamed_into_place(tmp_path, models_dir):
    calls: list[str] = []
    pinned = _pin(FAKE_GGUF)
    result = mf.fetch_file(pinned, models_dir, _local_opener(_source(tmp_path, FAKE_GGUF), calls), log=lambda _: None)
    assert result == "downloaded"
    assert calls == [pinned.url]
    assert (models_dir / pinned.target).read_bytes() == FAKE_GGUF
    assert sorted(p.name for p in models_dir.iterdir()) == [pinned.target]

    again = mf.fetch_file(pinned, models_dir, _no_download, log=lambda _: None)
    assert again == "present"


def test_a_hash_mismatch_fails_and_leaves_no_accepted_file(tmp_path, models_dir):
    pinned = _pin(FAKE_GGUF, sha256=hashlib.sha256(b"other bytes").hexdigest())
    with pytest.raises(mf.FetchError, match="SHA-256 mismatch"):
        mf.fetch_file(pinned, models_dir, _local_opener(_source(tmp_path, FAKE_GGUF), []), log=lambda _: None)
    assert list(models_dir.iterdir()) == []


def test_a_mismatching_download_also_removes_a_target_that_did_not_match_its_pin(tmp_path, models_dir):
    pinned = _pin(FAKE_GGUF, sha256=hashlib.sha256(b"other bytes").hexdigest())
    (models_dir / pinned.target).write_bytes(FAKE_GGUF[:-1] + b"x")
    with pytest.raises(mf.FetchError):
        mf.fetch_file(pinned, models_dir, _local_opener(_source(tmp_path, FAKE_GGUF), []), log=lambda _: None)
    assert list(models_dir.iterdir()) == []


def test_a_wrong_existing_file_is_replaced_by_a_verified_download(tmp_path, models_dir):
    pinned = _pin(FAKE_GGUF)
    (models_dir / pinned.target).write_bytes(FAKE_GGUF[:-1] + b"x")
    result = mf.fetch_file(pinned, models_dir, _local_opener(_source(tmp_path, FAKE_GGUF), []), log=lambda _: None)
    assert result == "downloaded"
    assert (models_dir / pinned.target).read_bytes() == FAKE_GGUF


@pytest.mark.parametrize("served", [FAKE_GGUF[:-10], FAKE_GGUF + b"extra"])
def test_a_truncated_or_oversized_download_is_refused(tmp_path, models_dir, served):
    pinned = _pin(FAKE_GGUF)
    with pytest.raises(mf.FetchError, match="refused"):
        mf.fetch_file(pinned, models_dir, _local_opener(_source(tmp_path, served), []), log=lambda _: None)
    assert list(models_dir.iterdir()) == []


def test_a_failing_source_leaves_nothing(models_dir):
    def broken(url: str, timeout: float):
        raise OSError("connection reset")

    with pytest.raises(mf.FetchError, match="download failed"):
        mf.fetch_file(_pin(FAKE_GGUF), models_dir, broken, log=lambda _: None)
    assert list(models_dir.iterdir()) == []


@pytest.mark.parametrize("name", ["model.bin", "model.pt", "model.pth", "model.pkl", "model.ckpt", "model.npy"])
def test_pickle_based_formats_are_refused_before_any_download(models_dir, name):
    with pytest.raises(mf.FetchError, match="pickle"):
        mf.fetch_file(_pin(FAKE_GGUF, target=name), models_dir, _no_download, log=lambda _: None)
    assert list(models_dir.iterdir()) == []


@pytest.mark.parametrize("name", ["model.bin", "model.pt", "model.pkl", "model.ckpt"])
def test_a_manifest_naming_a_pickle_based_format_is_refused(tmp_path, name):
    data = json.loads(mf.MANIFEST_PATH.read_text(encoding="utf-8"))
    data["models"][1]["files"][0]["target"] = name
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(mf.ManifestError, match="pickle"):
        mf.load_manifest(path)


def test_a_pickle_disguised_as_gguf_is_refused_by_content(tmp_path, models_dir):
    disguised = pickle.dumps({"weights": list(range(100))})
    pinned = _pin(disguised, target="disguised.gguf")
    with pytest.raises(mf.FetchError, match="not a valid .gguf"):
        mf.fetch_file(pinned, models_dir, _local_opener(_source(tmp_path, disguised), []), log=lambda _: None)
    assert list(models_dir.iterdir()) == []


def test_a_safetensors_header_is_accepted_by_content(tmp_path, models_dir):
    header = b'{"__metadata__":{}}'
    content = len(header).to_bytes(8, "little") + header
    pinned = _pin(content, target="weights.safetensors")
    assert mf.fetch_file(pinned, models_dir, _local_opener(_source(tmp_path, content), []), log=lambda _: None) == "downloaded"


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda f: f.update(revision="main"), "revision"),
        (lambda f: f.update(sha256="abc"), "sha256"),
        (lambda f: f.pop("sha256"), "sha256"),
        (lambda f: f.update(size_bytes=0), "size_bytes"),
        (lambda f: f.update(target="../escape.gguf"), "target"),
        (lambda f: f.update(target="sub/dir.gguf"), "target"),
        (lambda f: f.update(sha256="13cb7bebccbd04afc8f4090cb949ecf8937cdf7377c5799b1a0c594e7c0d3e17"), "different pins"),
    ],
)
def test_the_manifest_rejects_unpinned_or_unsafe_entries(tmp_path, change, message):
    data = json.loads(mf.MANIFEST_PATH.read_text(encoding="utf-8"))
    change(data["models"][1]["files"][1])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(mf.ManifestError, match=message):
        mf.load_manifest(path)


def test_only_https_is_opened_and_redirects_must_stay_on_https():
    with pytest.raises(mf.FetchError, match="https"):
        mf.https_open("http://127.0.0.1:9/file.gguf", 1)
    handler = mf._HttpsOnlyRedirects()
    request = mf.urllib.request.Request("https://huggingface.co/example/fake-GGUF/resolve/x/f.gguf")
    with pytest.raises(mf.FetchError, match="redirected"):
        handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "http://cdn.example/f.gguf")
    followed = handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "https://cdn.example/f.gguf")
    assert followed.full_url == "https://cdn.example/f.gguf"


def test_main_fetches_through_the_manifest_and_clears_stale_partials(tmp_path, models_dir):
    content = FAKE_GGUF
    manifest = {
        "default": "fake",
        "models": [{"id": "fake", "files": [{
            "repo": "example/fake-GGUF", "revision": "1" * 40, "file": "fake.gguf", "target": "fake.gguf",
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
        }]}],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    mf.partial_path(models_dir, "left-over.gguf").write_bytes(b"partial")
    calls: list[str] = []
    code = mf.main([], models_dir, manifest_path, _local_opener(_source(tmp_path, content), calls), log=lambda _: None)
    assert code == mf.EXIT_OK
    assert calls == ["https://huggingface.co/example/fake-GGUF/resolve/" + "1" * 40 + "/fake.gguf"]
    assert sorted(p.name for p in models_dir.iterdir()) == ["fake.gguf"]


def test_main_exit_codes_for_bad_names_a_missing_volume_and_a_full_disk(tmp_path, models_dir, monkeypatch):
    assert mf.main(["not-a-model"], models_dir, open_url=_no_download, log=lambda _: None) == mf.EXIT_INVALID
    assert mf.main([], tmp_path / "missing", open_url=_no_download, log=lambda _: None) == mf.EXIT_FAILED
    monkeypatch.setattr(mf.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 1})())
    assert mf.main([], models_dir, open_url=_no_download, log=lambda _: None) == mf.EXIT_FAILED
    assert list(models_dir.iterdir()) == []


def test_main_lists_the_models_without_touching_the_volume(tmp_path):
    lines: list[str] = []
    assert mf.main(["--list"], tmp_path / "missing", open_url=_no_download, log=lines.append) == mf.EXIT_OK
    assert [line.split(":")[0] for line in lines] == list(OWNER_PINS)
