"""Tests for the transport from `api` to the Codex sidecar, against an in-memory sidecar (httpx.MockTransport).

The fake sidecar checks each request the way docker/codex/sidecar-server.cjs does (token, argument
shape, allowed files) and answers from a plan. No network, CLI or account is used, and every image is
synthetic. The server side has its own tests in tests/codex-sidecar-server.test.cjs.
"""

import base64
import io
import json
import subprocess
from typing import Any, Callable

import httpx
import pytest
from PIL import Image

from src.providers import (
    COMPARE_PROMPT,
    CodexSettings,
    SidecarRunner,
    prepare_copy,
    sidecar_provider,
)
from src.providers.codex_sidecar import TOKEN_ENV
from src.utils import load_config

TOKEN = "t" * 64
GOOD_COMPARISON = {"winner": "A", "confidence": "high", "reasons": ["light"], "note": "Softer light."}


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="JPEG", quality=90)
    return prepare_copy(buffer.getvalue())


A_COPY = _jpeg((200, 40, 40))
B_COPY = _jpeg((40, 40, 200))


class FakeSidecar:
    """Records every request and answers each with `reply(body)`, by default a good comparison."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply: Callable[[dict[str, Any]], httpx.Response] = lambda _: httpx.Response(
            200, json={"exit_code": 0, "timed_out": False, "verdict": json.dumps(GOOD_COMPARISON)}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": "unauthorized"})
        return self.reply(json.loads(request.content))

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture()
def sidecar():
    return FakeSidecar()


def make_runner(sidecar: FakeSidecar, token: str = TOKEN) -> SidecarRunner:
    return SidecarRunner("http://codex:8090", token, transport=httpx.MockTransport(sidecar.handle))


def make_provider(sidecar: FakeSidecar, opted_in: bool = True, **overrides: Any):
    return sidecar_provider(
        CodexSettings.from_mapping({"retry_delay_seconds": 0, **overrides}),
        opted_in=opted_in,
        environ={TOKEN_ENV: TOKEN},
        sleep=lambda _: None,
        transport=httpx.MockTransport(sidecar.handle),
    )


def test_a_comparison_travels_to_the_sidecar_and_back(sidecar):
    result = make_provider(sidecar).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert result.ok and result.winner == "A" and result.attempts == 1
    [request] = sidecar.requests
    assert request.method == "POST" and request.url.path == "/v1/run"
    assert request.url.host == "codex" and request.url.port == 8090
    [body] = sidecar.bodies
    assert body["args"][0] == "exec" and "codex" not in body["args"]
    assert sorted(body["files"]) == ["a.jpg", "b.jpg", "schema.json"]
    assert base64.b64decode(body["files"]["a.jpg"]) == A_COPY
    assert base64.b64decode(body["files"]["b.jpg"]) == B_COPY
    assert json.loads(base64.b64decode(body["files"]["schema.json"]))["type"] == "object"
    assert body["timeout_seconds"] == 180.0


def test_the_token_travels_only_in_the_header_and_no_key_or_path_is_sent(sidecar, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-not-for-the-sidecar")
    monkeypatch.setenv("AURAFRAME_PHOTOS_DIR", str(tmp_path / "secret-trip"))
    assert make_provider(sidecar).compare(A_COPY, B_COPY, COMPARE_PROMPT).ok
    [request] = sidecar.requests
    raw = request.content.decode("utf-8")
    for secret in (TOKEN, "sk-secret", "secret-trip", str(tmp_path)):
        assert secret not in raw and secret not in str(request.url)
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_the_runner_sends_only_the_allowed_files_and_writes_the_verdict(sidecar, tmp_path):
    for name in ("a.jpg", "schema.json", "notes.txt", "verdict.json.bak"):
        (tmp_path / name).write_bytes(b"x")
    sidecar.reply = lambda _: httpx.Response(200, json={"exit_code": 0, "timed_out": False, "verdict": "{}"})
    assert make_runner(sidecar)(["codex", "exec", "x"], tmp_path, 5.0) == 0
    assert sorted(sidecar.bodies[0]["files"]) == ["a.jpg", "schema.json"]
    assert (tmp_path / "verdict.json").read_text() == "{}"


def test_the_version_check_needs_no_files(sidecar, tmp_path):
    sidecar.reply = lambda _: httpx.Response(200, json={"exit_code": 0, "timed_out": False, "verdict": None})
    health = make_provider(sidecar).health()
    assert health.ready
    [body] = sidecar.bodies
    assert body["args"] == ["--version"] and body["files"] == {}


def test_a_non_zero_exit_is_returned_and_no_verdict_is_written(sidecar, tmp_path):
    sidecar.reply = lambda _: httpx.Response(200, json={"exit_code": 3, "timed_out": False, "verdict": None})
    assert make_runner(sidecar)(["codex", "exec", "x"], tmp_path, 5.0) == 3
    assert not (tmp_path / "verdict.json").exists()


def test_an_oversized_verdict_is_dropped(sidecar, tmp_path):
    sidecar.reply = lambda _: httpx.Response(
        200, json={"exit_code": 0, "timed_out": False, "verdict": "x" * 70000}
    )
    assert make_runner(sidecar)(["codex", "exec", "x"], tmp_path, 5.0) == 0
    assert not (tmp_path / "verdict.json").exists()


def test_a_timeout_in_the_sidecar_or_on_the_wire_is_a_timeout(sidecar, tmp_path):
    sidecar.reply = lambda _: httpx.Response(200, json={"exit_code": -1, "timed_out": True, "verdict": None})
    with pytest.raises(subprocess.TimeoutExpired):
        make_runner(sidecar)(["codex", "exec", "x"], tmp_path, 5.0)

    def slow(_: dict[str, Any]) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    sidecar.reply = slow
    with pytest.raises(subprocess.TimeoutExpired):
        make_runner(sidecar)(["codex", "exec", "x"], tmp_path, 5.0)
    provider = make_provider(sidecar, max_retries=0)
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("timeout")


@pytest.mark.parametrize(
    "reply",
    [
        lambda _: httpx.Response(400, json={"error": "refused"}),
        lambda _: httpx.Response(409, json={"error": "busy"}),
        lambda _: httpx.Response(502, json={"error": "cli_not_started"}),
        lambda _: httpx.Response(200, text="not json"),
        lambda _: httpx.Response(200, json=["a", "list"]),
        lambda _: httpx.Response(200, json={"exit_code": True, "timed_out": False, "verdict": None}),
        lambda _: httpx.Response(200, json={"exit_code": 0, "verdict": None}),
        lambda _: httpx.Response(200, json={"exit_code": 0, "timed_out": False, "verdict": 5}),
    ],
)
def test_a_refusal_or_unknown_answer_is_an_os_error(sidecar, tmp_path, reply):
    sidecar.reply = reply
    with pytest.raises(OSError):
        make_runner(sidecar)(["codex", "exec", "x"], tmp_path, 5.0)


def test_a_missing_sidecar_is_unreachable_and_not_retried(sidecar):
    def down(body: dict[str, Any]) -> httpx.Response:
        raise httpx.ConnectError("refused")

    sidecar.reply = down
    provider = make_provider(sidecar, max_retries=3)
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("unreachable") and result.attempts == 1
    assert not provider.health().ready


def test_a_wrong_token_is_refused_by_the_sidecar(sidecar, tmp_path):
    with pytest.raises(OSError):
        make_runner(sidecar, token="w" * 64)(["codex", "--version"], tmp_path, 5.0)


def test_without_opt_in_nothing_reaches_the_sidecar(sidecar):
    provider = make_provider(sidecar, opted_in=False)
    assert not provider.compare(A_COPY, B_COPY, COMPARE_PROMPT).ok
    assert not provider.health().ready
    assert sidecar.requests == []


@pytest.mark.parametrize("environ", [{}, {TOKEN_ENV: ""}, {TOKEN_ENV: "short"}])
def test_a_missing_or_short_token_refuses_to_build(environ):
    with pytest.raises(ValueError):
        sidecar_provider(CodexSettings.from_mapping({}), opted_in=True, environ=environ)


def test_the_endpoint_must_be_http():
    with pytest.raises(ValueError):
        SidecarRunner("file:///exchange", TOKEN)
    with pytest.raises(ValueError):
        CodexSettings.from_mapping({"endpoint": "codex:8090"})
    assert CodexSettings.from_mapping({"endpoint": "http://codex:8090/"}).endpoint == "http://codex:8090"


def test_engine_config_points_at_the_sidecar_service():
    settings = CodexSettings.from_mapping(load_config()["ai"]["codex"])
    assert settings.endpoint == "http://codex:8090"
