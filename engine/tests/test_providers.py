"""Tests for the VisionProvider contract and the llama-server provider.

A fake llama-server runs on 127.0.0.1 in a thread, so the real HTTP client, timeouts and
retries are exercised. Every image is synthetic. No model is needed and no photo is read.
"""

import base64
import io
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from src.providers import (
    COMPARE_PROMPT,
    CRITIQUE_PROMPT,
    GuardedProvider,
    LlamaServerProvider,
    ProviderSettings,
    compare_or_prescore,
    prepare_copy,
)
from src.providers.contract import (
    CALL_LIMIT_BOUNDS,
    RETRY_BOUNDS,
    TIMEOUT_BOUNDS,
    TOKEN_BOUNDS,
)
from src.providers.images import JPEG_QUALITY, MAX_SIDE, check_copy
from src.providers.schemas import COMPARISON_SCHEMA, validate
from src.utils import load_config

GOOD_COMPARISON = {"winner": "A", "confidence": "high", "reasons": ["light"], "note": "Softer light."}
GOOD_CRITIQUE = {"strengths": ["clean light"], "weaknesses": ["tilted horizon"], "caption": "A quiet street."}
MODEL_FILE = "Qwen-test.gguf"


def chat_reply(content: Any, status: int = 200, delay: float = 0.0) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    body = {"choices": [{"message": {"content": text}}]} if status == 200 else {"error": {"message": "x"}}
    return {"status": status, "body": body, "delay": delay}


class FakeServer:
    """A scripted llama-server: chat replies are used in order and the last one repeats."""

    def __init__(self) -> None:
        self.replies: list[dict[str, Any]] = [chat_reply(GOOD_COMPARISON)]
        self.health_status = 200
        self.models: list[str] = [f"/models/{MODEL_FILE}"]
        self.requests: list[tuple[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append((self.path, None))
                if self.path == "/health":
                    self._send(outer.health_status, {"status": "ok"})
                elif self.path == "/v1/models":
                    self._send(200, {"data": [{"id": m} for m in outer.models]})
                else:
                    self._send(404, {})

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                index = min(len(outer.chat_requests) - 1, len(outer.replies) - 1)
                reply = outer.replies[index]
                time.sleep(reply["delay"])
                try:
                    self._send(reply["status"], reply["body"])
                except OSError:
                    pass  # the client gave up after its timeout

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def chat_requests(self) -> list[Any]:
        return [body for path, body in self.requests if path == "/v1/chat/completions"]

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def server():
    fake = FakeServer()
    yield fake
    fake.stop()


# Builds a provider against the fake server with no real sleeping between retries.
def make_provider(server: FakeServer, **overrides: Any) -> LlamaServerProvider:
    values = {"endpoint": server.url, "model": MODEL_FILE, "retry_delay_seconds": 0, **overrides}
    return LlamaServerProvider(ProviderSettings.from_mapping(values), sleep=lambda _: None)


# Draws a synthetic photo copy that satisfies the contract.
def copy_of(color: tuple[int, int, int], size: tuple[int, int] = (640, 480)) -> bytes:
    return prepare_copy(_jpeg(Image.new("RGB", size, color)))


def _jpeg(image: Image.Image, **options: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, **options)
    return buffer.getvalue()


A_COPY = copy_of((200, 40, 40))
B_COPY = copy_of((40, 40, 200))


def test_compare_valid_output_is_typed(server):
    result = make_provider(server).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert result.ok and result.error is None
    assert (result.winner, result.confidence, result.reasons) == ("A", "high", ("light",))
    assert result.note == "Softer light."
    assert result.attempts == 1 and result.provider == "llama-server"


def test_critique_valid_output_is_typed(server):
    server.replies = [chat_reply(GOOD_CRITIQUE)]
    result = make_provider(server).critique(A_COPY, CRITIQUE_PROMPT)
    assert result.ok
    assert result.strengths == ("clean light",) and result.weaknesses == ("tilted horizon",)
    assert result.caption == "A quiet street."


def test_prompt_version_is_in_results_and_request(server):
    provider = make_provider(server)
    assert COMPARE_PROMPT.version and CRITIQUE_PROMPT.version != COMPARE_PROMPT.version
    assert provider.compare(A_COPY, B_COPY, COMPARE_PROMPT).prompt_version == COMPARE_PROMPT.version
    server.replies = [chat_reply(GOOD_CRITIQUE)]
    assert provider.critique(A_COPY, CRITIQUE_PROMPT).prompt_version == CRITIQUE_PROMPT.version
    body = server.chat_requests[0]
    assert body["messages"][0]["content"][0]["text"] == COMPARE_PROMPT.text


def test_request_is_greedy_schema_constrained_and_anonymous(server):
    make_provider(server).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    body = server.chat_requests[0]
    assert body["temperature"] == 0 and body["stream"] is False
    assert body["response_format"]["json_schema"]["schema"] == COMPARISON_SCHEMA
    urls = [p["image_url"]["url"] for p in body["messages"][0]["content"] if p["type"] == "image_url"]
    sent = [base64.b64decode(u.split(",", 1)[1]) for u in urls]
    assert sent == [A_COPY, B_COPY]
    assert sorted(body) == [
        "max_tokens", "messages", "model", "response_format", "stream", "temperature",
    ]


def test_invalid_json_is_retried_then_succeeds(server):
    server.replies = [chat_reply("not json at all"), chat_reply(GOOD_COMPARISON)]
    result = make_provider(server).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert result.ok and result.attempts == 2


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        {"winner": "C", "confidence": "high", "reasons": [], "note": ""},
        {"winner": "A", "confidence": "high", "reasons": ["vibes"], "note": ""},
        {"winner": "A", "confidence": "high", "reasons": [], "note": "x" * 500},
        {"winner": "A", "confidence": "high", "reasons": [], "note": "", "extra": 1},
        {"winner": "A"},
        ["A"],
    ],
)
def test_invalid_output_is_bounded_and_recorded_as_failed(server, bad):
    server.replies = [chat_reply(bad)]
    result = make_provider(server, max_retries=2).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.winner is None
    assert result.error.startswith("invalid_output")
    assert result.attempts == 3 and len(server.chat_requests) == 3


def test_failure_message_never_echoes_model_text(server):
    server.replies = [chat_reply({"winner": "SECRET-MODEL-TEXT"})]
    result = make_provider(server, max_retries=0).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and "SECRET-MODEL-TEXT" not in result.error


def test_timeout_fails_within_the_bound(server):
    server.replies = [chat_reply(GOOD_COMPARISON, delay=1.5)]
    provider = make_provider(server, timeout_seconds=0.3, max_retries=0)
    started = time.monotonic()
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert time.monotonic() - started < 1.2
    assert not result.ok and result.error.startswith("timeout")


def test_timeout_is_retried_a_bounded_number_of_times(server):
    server.replies = [chat_reply(GOOD_COMPARISON, delay=1.0)]
    result = make_provider(server, timeout_seconds=0.25, max_retries=1).compare(
        A_COPY, B_COPY, COMPARE_PROMPT
    )
    assert not result.ok and result.attempts == 2


def test_missing_model_is_reported_by_health(server):
    server.models = []
    health = make_provider(server).health()
    assert health.reachable and not health.model_available and not health.ready
    assert "no model loaded" in health.message and "docs/operations.md" in health.message


def test_other_model_loaded_is_reported_by_health(server):
    server.models = ["/models/another.gguf"]
    assert not make_provider(server).health().model_available


def test_health_ready_when_model_is_loaded(server):
    assert make_provider(server).health().ready
    assert make_provider(server, model="").health().ready


def test_health_while_loading(server):
    server.health_status = 503
    health = make_provider(server).health()
    assert health.reachable and not health.ready and "loading" in health.message


def test_health_when_unreachable():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    provider = LlamaServerProvider(
        ProviderSettings.from_mapping(
            {"endpoint": f"http://127.0.0.1:{port}", "timeout_seconds": 1, "retry_delay_seconds": 0}
        )
    )
    health = provider.health()
    assert not health.reachable and "./start.sh" in health.message
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("unreachable")


def test_missing_model_on_a_call_fails_without_retry(server):
    server.replies = [chat_reply("", status=404)]
    result = make_provider(server, max_retries=3).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("model_missing")
    assert result.attempts == 1 and len(server.chat_requests) == 1


def test_server_error_is_retried(server):
    server.replies = [chat_reply("", status=503), chat_reply(GOOD_COMPARISON)]
    assert make_provider(server).compare(A_COPY, B_COPY, COMPARE_PROMPT).attempts == 2


def test_unprepared_original_never_reaches_the_server(server, tmp_path):
    big = tmp_path / "original.jpg"
    big.write_bytes(_jpeg(Image.new("RGB", (3000, 2000), "white")))
    result = make_provider(server).compare(big.read_bytes(), B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("invalid_input") and result.attempts == 0
    with_exif = Image.Exif()
    with_exif[0x010E] = "a description"
    tagged = _jpeg(Image.new("RGB", (64, 64)), exif=with_exif.tobytes())
    assert not make_provider(server).critique(tagged, CRITIQUE_PROMPT).ok
    assert not make_provider(server).critique(b"not an image", CRITIQUE_PROMPT).ok
    assert server.chat_requests == []


def test_prepared_copy_is_1024_px_jpeg_q90_without_metadata(tmp_path):
    exif = Image.Exif()
    exif[0x010E] = "secret-holiday-description"
    exif[0x013B] = "secret-artist"
    exif[0x0112] = 1
    source = tmp_path / "secret_name_holiday.jpg"
    Image.effect_noise((3000, 2000), 40).convert("RGB").save(
        source, "JPEG", quality=95, exif=exif.tobytes(), comment=b"secret-comment"
    )
    assert b"secret-holiday-description" in source.read_bytes()

    data = prepare_copy(source)

    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size[0] == MAX_SIDE and abs(image.size[1] - 683) <= 1
        assert len(image.getexif()) == 0
        assert not ({"exif", "icc_profile", "comment"} & set(image.info))
        # The quantization tables identify the quality setting.
        reference = io.BytesIO()
        Image.new("RGB", image.size).save(reference, "JPEG", quality=JPEG_QUALITY)
        with Image.open(io.BytesIO(reference.getvalue())) as expected:
            assert image.quantization == expected.quantization
    for secret in (b"Exif", b"secret", b"holiday", str(tmp_path).encode(), tmp_path.name.encode()):
        assert secret not in data
    check_copy(data)


def test_prepare_copy_applies_orientation_and_does_not_enlarge():
    exif = Image.Exif()
    exif[0x0112] = 6  # stored sideways: displayed rotated by 90 degrees
    sideways = _jpeg(Image.new("RGB", (400, 200), "gray"), exif=exif.tobytes())
    with Image.open(io.BytesIO(prepare_copy(sideways))) as image:
        assert image.size == (200, 400)


def test_settings_are_clamped_to_their_bounds():
    huge = ProviderSettings.from_mapping(
        {"timeout_seconds": 10**6, "max_retries": 99, "max_calls": 10**9, "max_tokens": 10**6,
         "max_consecutive_failures": 10**6, "retry_delay_seconds": 10**6}
    )
    assert huge.timeout_seconds == TIMEOUT_BOUNDS[1] and huge.max_retries == RETRY_BOUNDS[1]
    assert huge.max_calls == CALL_LIMIT_BOUNDS[1] and huge.max_tokens == TOKEN_BOUNDS[1]
    assert huge.max_consecutive_failures == 20 and huge.retry_delay_seconds == 10.0
    tiny = ProviderSettings.from_mapping(
        {"timeout_seconds": 0, "max_retries": -4, "max_calls": 0, "max_tokens": 1}
    )
    assert tiny.timeout_seconds == TIMEOUT_BOUNDS[0] and tiny.max_retries == 0
    assert tiny.max_calls == 1 and tiny.max_tokens == TOKEN_BOUNDS[0]


def test_settings_fall_back_to_defaults_for_unusable_values():
    odd = ProviderSettings.from_mapping({"max_retries": "many", "timeout_seconds": True, "max_calls": None})
    assert odd == ProviderSettings.from_mapping({})
    assert ProviderSettings.from_mapping({"timeout_seconds": float("nan")}).timeout_seconds == 120.0


def test_settings_reject_a_non_http_endpoint():
    with pytest.raises(ValueError):
        ProviderSettings.from_mapping({"endpoint": "file:///etc/passwd"})


def test_engine_config_ai_local_loads_within_bounds():
    settings = ProviderSettings.from_mapping(load_config()["ai"]["local"])
    assert settings.endpoint.startswith("http://") and settings.max_retries <= RETRY_BOUNDS[1]


def test_failed_provider_degrades_to_the_prescore_without_stopping(server):
    server.replies = [chat_reply("garbage")]
    settings = ProviderSettings.from_mapping(
        {"endpoint": server.url, "max_retries": 0, "max_consecutive_failures": 2, "retry_delay_seconds": 0}
    )
    guarded = GuardedProvider(LlamaServerProvider(settings, sleep=lambda _: None), settings)
    verdicts = [
        compare_or_prescore(guarded, A_COPY, B_COPY, COMPARE_PROMPT, (80.0, 60.0)),
        compare_or_prescore(guarded, A_COPY, B_COPY, COMPARE_PROMPT, (50.0, 70.0)),
        compare_or_prescore(guarded, A_COPY, B_COPY, COMPARE_PROMPT, (50.0, 50.5)),
    ]
    assert [v.winner for v in verdicts] == ["A", "B", "tie"]
    assert all(v.ok and v.provider == "prescore" for v in verdicts)
    assert guarded.status.degraded and "2 times in a row" in guarded.status.reason
    # After the breaker opened, the third call never reached the server.
    assert len(server.chat_requests) == 2 and guarded.status.calls == 2


def test_success_resets_the_failure_streak(server):
    server.replies = [chat_reply("garbage"), chat_reply(GOOD_COMPARISON)] * 3
    settings = ProviderSettings.from_mapping(
        {"endpoint": server.url, "max_retries": 0, "max_consecutive_failures": 2, "retry_delay_seconds": 0}
    )
    guarded = GuardedProvider(LlamaServerProvider(settings, sleep=lambda _: None), settings)
    # Replies alternate by request index: bad, good, bad, ...
    for _ in range(4):
        guarded.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not guarded.status.degraded and guarded.status.failures == 2


def test_call_limit_degrades_the_provider(server):
    settings = ProviderSettings.from_mapping(
        {"endpoint": server.url, "max_calls": 2, "retry_delay_seconds": 0}
    )
    guarded = GuardedProvider(LlamaServerProvider(settings), settings)
    results = [guarded.compare(A_COPY, B_COPY, COMPARE_PROMPT) for _ in range(3)]
    assert [r.ok for r in results] == [True, True, False]
    assert results[2].error.startswith("call_limit")
    assert guarded.status.degraded and len(server.chat_requests) == 2
    assert not guarded.critique(A_COPY, CRITIQUE_PROMPT).ok


def test_guard_absorbs_a_provider_that_raises():
    class Broken:
        def health(self):
            raise RuntimeError("boom")

        def compare(self, first, second, prompt):
            raise RuntimeError("boom")

        def critique(self, image, prompt):
            raise RuntimeError("boom")

    guarded = GuardedProvider(Broken(), ProviderSettings())
    assert not guarded.health().reachable
    assert not guarded.compare(A_COPY, B_COPY, COMPARE_PROMPT).ok
    assert not guarded.critique(A_COPY, CRITIQUE_PROMPT).ok
    assert guarded.status.failures == 2


def test_validator_rejects_unknown_keywords_and_booleans_as_numbers():
    with pytest.raises(ValueError):
        validate({}, {"type": "object", "pattern": "x"})
    assert validate(True, {"type": "integer"}) == ["$: expected integer"]
    assert validate(1, {"type": "number"}) == []
    assert validate({"a": []}, {"type": "object", "required": ["b"]}) == ["$.b: missing"]
