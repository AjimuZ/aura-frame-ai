"""Downloads the pinned model files into the models volume, and accepts a file only after its SHA-256 matches.

Each file is pinned in manifest.json by repository, commit revision, file name, size and SHA-256. A download
goes to a hidden partial file beside its target. It becomes the target by an atomic rename only after the
size, the SHA-256 and the file format are checked. On any failure the partial file is removed, so a target
name only ever holds a verified file.

Only GGUF and safetensors are accepted. Pickle-based formats can run code when they are loaded, so they are
refused by name before any download, and by content after it.

Standard library only: this module runs alone in the fetch-models image, apart from the engine.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
DEFAULT_MODELS_DIR = Path("/models")
BASE_URL = "https://huggingface.co"
ALLOWED_SUFFIXES = (".gguf", ".safetensors")
# Formats that unpickle when loaded. Named only so that a refusal says why; the allow-list above decides.
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".pkl", ".pickle", ".ckpt", ".joblib", ".npy", ".npz")
CHUNK_BYTES = 1024 * 1024
READ_TIMEOUT_SECONDS = 60
USER_AGENT = "auraframe-fetch-models"
GGUF_MAGIC = b"GGUF"
# The safetensors reference loader refuses headers above 100 MB.
MAX_SAFETENSORS_HEADER = 100 * 1024 * 1024

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2

_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

OpenUrl = Callable[[str, float], BinaryIO]
Log = Callable[[str], None]


class FetchError(Exception):
    """A download or a check failed. The message names no host path and is safe to print."""


class ManifestError(ValueError):
    """The manifest, or a requested model name, is invalid. Nothing has been downloaded."""


@dataclass(frozen=True)
class PinnedFile:
    """One file at one repository revision, with the size and SHA-256 it must have."""

    repo: str
    revision: str
    file: str
    target: str
    sha256: str
    size_bytes: int

    # Returns the download URL of this exact revision of the file; a branch name is never used.
    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.repo}/resolve/{self.revision}/{urllib.parse.quote(self.file)}"


@dataclass(frozen=True)
class Model:
    """A model build: its id on the command line, a label, and the files llama-server needs for it."""

    id: str
    label: str
    files: tuple[PinnedFile, ...]


@dataclass(frozen=True)
class Manifest:
    """The validated manifest: the default model id and every model, in manifest order."""

    default: str
    models: tuple[Model, ...]


# Returns why a file name is not an accepted model format, or None when it is GGUF or safetensors.
def format_problem(name: str) -> str | None:
    suffix = Path(name).suffix.lower()
    if suffix in ALLOWED_SUFFIXES:
        return None
    if suffix in PICKLE_SUFFIXES:
        return f"{name}: pickle-based formats can run code when loaded and are refused"
    return f"{name}: only {' and '.join(ALLOWED_SUFFIXES)} files are accepted"


# Returns one validated pinned file from its manifest entry. Raises ManifestError naming the first problem.
def _pinned_file(entry: object, where: str) -> PinnedFile:
    if not isinstance(entry, dict):
        raise ManifestError(f"{where}: a file entry must be an object")
    fields = {key: entry.get(key) for key in ("repo", "revision", "file", "target", "sha256", "size_bytes")}
    checks = (
        ("repo", _REPO),
        ("revision", _REVISION),
        ("file", _FILE_NAME),
        ("target", _FILE_NAME),
        ("sha256", _SHA256),
    )
    for key, pattern in checks:
        if not isinstance(fields[key], str) or not pattern.fullmatch(fields[key]):
            raise ManifestError(f"{where}: '{key}' is missing or malformed")
    size = fields["size_bytes"]
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ManifestError(f"{where}: 'size_bytes' must be a positive integer")
    for key in ("file", "target"):
        problem = format_problem(fields[key])
        if problem:
            raise ManifestError(f"{where}: {problem}")
    return PinnedFile(**fields)


# Reads and validates the manifest. Every file must name a repository, a 40-hex commit, a 64-hex SHA-256, a
# positive size and accepted formats; a target shared by two models must carry the same pin in both.
# Raises ManifestError on the first problem.
def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"the manifest cannot be read: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("models"), list) or not data["models"]:
        raise ManifestError("the manifest must hold a non-empty 'models' list")
    models: list[Model] = []
    by_target: dict[str, PinnedFile] = {}
    for index, raw in enumerate(data["models"]):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not _MODEL_ID.fullmatch(raw["id"]):
            raise ManifestError(f"model {index}: 'id' is missing or malformed")
        if any(model.id == raw["id"] for model in models):
            raise ManifestError(f"model {raw['id']}: the id appears twice")
        if not isinstance(raw.get("files"), list) or not raw["files"]:
            raise ManifestError(f"model {raw['id']}: 'files' must be a non-empty list")
        files = tuple(_pinned_file(entry, f"model {raw['id']}") for entry in raw["files"])
        for pinned in files:
            if by_target.setdefault(pinned.target, pinned) != pinned:
                raise ManifestError(f"{pinned.target}: two entries write this target with different pins")
        models.append(Model(id=raw["id"], label=str(raw.get("label", raw["id"])), files=files))
    default = data.get("default")
    if not any(model.id == default for model in models):
        raise ManifestError("'default' must name a model of the manifest")
    return Manifest(default=default, models=tuple(models))


# Returns the files to fetch for the requested model ids, each target once, in manifest order.
# No name means the default model; 'all' means every model. Raises ManifestError for an unknown id.
def select_files(manifest: Manifest, names: Iterable[str]) -> list[PinnedFile]:
    wanted = list(names) or [manifest.default]
    known = {model.id: model for model in manifest.models}
    if "all" in wanted:
        wanted = list(known)
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise ManifestError(f"unknown model '{unknown[0]}'; known: {', '.join(known)}, all")
    selected: dict[str, PinnedFile] = {}
    for model in manifest.models:
        if model.id in wanted:
            for pinned in model.files:
                selected.setdefault(pinned.target, pinned)
    return list(selected.values())


# Returns the SHA-256 of a file as 64 lowercase hex characters, reading it in chunks.
def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Raises FetchError unless the first bytes of `path` are those of the format that `name` claims: the GGUF magic,
# or a safetensors header (a little-endian length followed by a JSON object). `name` is the target name, since a
# partial file carries another suffix. A pickle renamed to .gguf therefore fails here.
def check_content_format(path: Path, name: str) -> None:
    suffix = Path(name).suffix.lower()
    with open(path, "rb") as handle:
        head = handle.read(9)
    if suffix == ".gguf" and head[:4] == GGUF_MAGIC:
        return
    if suffix == ".safetensors" and len(head) == 9:
        header_length = int.from_bytes(head[:8], "little")
        if 2 <= header_length <= MAX_SAFETENSORS_HEADER and head[8:9] == b"{":
            return
    raise FetchError(f"{name}: the content is not a valid {suffix or 'model'} file and is refused")


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another https URL. The pinned SHA-256 protects the content on any host."""

    # Returns the redirected request, or raises FetchError when the new URL is not https.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise FetchError("a download was redirected away from https; nothing was saved")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Opens an https URL for reading, following https redirects only. Raises FetchError for any other scheme and
# lets urllib's errors (HTTP status, network, timeout) propagate to the caller.
def https_open(url: str, timeout: float) -> BinaryIO:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise FetchError("only https downloads are allowed")
    opener = urllib.request.build_opener(_HttpsOnlyRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return opener.open(request, timeout=timeout)


# Prints one progress line at once, so `compose run` shows it live.
def _log(message: str) -> None:
    print(f"fetch-models: {message}", flush=True)


# Returns the hidden partial path of a target. Accepted names never start with a dot, so the two never meet.
def partial_path(models_dir: Path, target: str) -> Path:
    return models_dir / f".{target}.partial"


# Streams the pinned file into `partial`, which must not exist, and returns its SHA-256. Raises FetchError when
# the source fails or sends more or fewer bytes than pinned. The partial file is left for the caller to remove.
def _download(pinned: PinnedFile, partial: Path, open_url: OpenUrl, log: Log) -> str:
    digest = hashlib.sha256()
    received = 0
    next_report = 10
    try:
        with open_url(pinned.url, READ_TIMEOUT_SECONDS) as source, open(partial, "xb") as sink:
            for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                received += len(chunk)
                if received > pinned.size_bytes:
                    raise FetchError(f"{pinned.target}: the source sent more bytes than pinned; refused")
                digest.update(chunk)
                sink.write(chunk)
                if received * 100 >= next_report * pinned.size_bytes:
                    log(f"{pinned.target}: {received * 100 // pinned.size_bytes}%")
                    next_report = received * 100 // pinned.size_bytes + 10
            sink.flush()
            os.fsync(sink.fileno())
    except FetchError:
        raise
    except Exception as exc:  # Network, HTTP and file errors all mean: nothing is accepted.
        raise FetchError(f"{pinned.target}: the download failed ({exc.__class__.__name__}: {exc})") from exc
    if received != pinned.size_bytes:
        raise FetchError(f"{pinned.target}: received {received} bytes, pinned {pinned.size_bytes}; refused")
    return digest.hexdigest()


# Makes sure the target holds the pinned file and returns "present" when it already did or "downloaded".
# The download is checked for size, SHA-256 and format before an atomic rename makes it the target. Raises
# FetchError on any failure; the partial file is then gone, and so is a target that did not match its pin.
def fetch_file(pinned: PinnedFile, models_dir: Path, open_url: OpenUrl = https_open, log: Log = _log) -> str:
    problem = format_problem(pinned.target) or format_problem(pinned.file)
    if problem:
        raise FetchError(problem)
    target = models_dir / pinned.target
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise FetchError(f"{pinned.target}: the existing entry is not a regular file; remove it first")
    if target.exists():
        if target.stat().st_size == pinned.size_bytes and sha256_of(target) == pinned.sha256:
            check_content_format(target, pinned.target)
            log(f"{pinned.target}: already present and verified")
            return "present"
        log(f"{pinned.target}: the existing file does not match its pin; it is removed and downloaded again")
        target.unlink()
    partial = partial_path(models_dir, pinned.target)
    partial.unlink(missing_ok=True)
    log(f"{pinned.target}: downloading {pinned.size_bytes} bytes from {pinned.repo} at {pinned.revision[:12]}")
    try:
        actual = _download(pinned, partial, open_url, log)
        if actual != pinned.sha256:
            raise FetchError(f"{pinned.target}: SHA-256 mismatch (expected {pinned.sha256}, got {actual}); refused")
        check_content_format(partial, pinned.target)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    _sync_directory(models_dir)
    log(f"{pinned.target}: verified and saved")
    return "downloaded"


# Flushes the directory entry of a rename to disk where the platform allows it; a failure here changes nothing.
def _sync_directory(directory: Path) -> None:
    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


# Raises FetchError unless this user can create a file in the models directory. A volume created by another
# service may belong to root; the message then points to the remedy in the operations guide.
def ensure_writable(models_dir: Path) -> None:
    if not models_dir.is_dir():
        raise FetchError("the models directory does not exist")
    probe = models_dir / ".write-probe"
    try:
        handle = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(handle)
        probe.unlink()
    except OSError as exc:
        raise FetchError("the models volume is not writable by this user (see docs/operations.md#model-files)") from exc


# Removes partial files left by an interrupted run. Accepted files are never touched.
def remove_stale_partials(models_dir: Path) -> None:
    for entry in models_dir.glob(".*.partial"):
        if entry.is_file() and not entry.is_symlink():
            entry.unlink()


# Raises FetchError when the volume cannot hold the files that are still missing or wrong.
def ensure_space(models_dir: Path, files: Iterable[PinnedFile]) -> None:
    needed = 0
    for pinned in files:
        target = models_dir / pinned.target
        if not (target.is_file() and target.stat().st_size == pinned.size_bytes):
            needed += pinned.size_bytes
    free = shutil.disk_usage(models_dir).free
    if needed > free:
        raise FetchError(f"the models volume has {free} bytes free and needs {needed}; nothing was downloaded")


# Parses the command line. Positional names are model ids from the manifest, or 'all'.
def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fetch-models", description="Download the pinned model files and check their SHA-256."
    )
    parser.add_argument("models", nargs="*", help="model ids from the manifest, or 'all'; default: the default model")
    parser.add_argument("--list", action="store_true", help="list the models of the manifest and exit")
    return parser.parse_args(argv)


# Runs the command and returns its exit code: 0 when every selected file is verified in place, 1 when a
# download or check failed, 2 for an invalid manifest or model name. Files verified before a failure stay.
def main(
    argv: list[str] | None = None,
    models_dir: Path = DEFAULT_MODELS_DIR,
    manifest_path: Path = MANIFEST_PATH,
    open_url: OpenUrl = https_open,
    log: Log = _log,
) -> int:
    try:
        args = _parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_INVALID
    try:
        manifest = load_manifest(manifest_path)
        if args.list:
            for model in manifest.models:
                size = sum(pinned.size_bytes for pinned in model.files)
                log(f"{model.id}: {model.label}, {size / 1e9:.1f} GB")
            return EXIT_OK
        files = select_files(manifest, args.models)
    except ManifestError as exc:
        log(f"error: {exc}")
        return EXIT_INVALID
    try:
        ensure_writable(models_dir)
        remove_stale_partials(models_dir)
        ensure_space(models_dir, files)
        for pinned in files:
            fetch_file(pinned, models_dir, open_url, log)
    except FetchError as exc:
        log(f"error: {exc}")
        return EXIT_FAILED
    log(f"done: {len(files)} file(s) verified in the models volume")
    return EXIT_OK
