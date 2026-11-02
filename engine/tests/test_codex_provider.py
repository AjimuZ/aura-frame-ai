"""Tests for the opt-in Codex provider, against a fake CLI started as a real subprocess.

The fake CLI is a small Python script written into pytest's temporary folder. It follows a plan
(write a verdict, exit with a code, sleep, write nothing) and records what it received: argv,
environment, working folder and the bytes of every attached image. No real CLI, account or
network is used, and every image is synthetic.
"""

import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from src.providers import (
    COMPARE_PROMPT,
    CRITIQUE_PROMPT,
    CodexProvider,
    CodexSettings,
    GuardedProvider,
    compare_or_prescore,
    prepare_copy,
)
from src.providers.codex import run_cli
from src.providers.contract import CALL_LIMIT_BOUNDS, RETRY_BOUNDS, TIMEOUT_BOUNDS
from src.providers.images import check_copy
from src.providers.schemas import COMPARISON_SCHEMA, CRITIQUE_SCHEMA
from src.utils import load_config

GOOD_COMPARISON = {"winner": "B", "confidence": "medium", "reasons": ["light", "color"], "note": "Warmer light."}
GOOD_CRITIQUE = {"strengths": ["clean lines"], "weaknesses": ["flat sky"], "caption": "A bridge at dusk."}

FAKE_CLI = r'''
import base64, json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.0.0-fake")
    sys.exit(0)
log = HERE / "calls"
log.mkdir(exist_ok=True)
index = len(list(log.iterdir()))
plan = json.loads((HERE / "plan.json").read_text())
step = plan[min(index, len(plan) - 1)]

def values(flag):
    return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == flag]

record = {
    "argv": args,
    "env": dict(os.environ),
    "cwd": os.getcwd(),
    "listing": sorted(os.listdir(".")),
    "images": [base64.b64encode(Path(p).read_bytes()).decode() for p in values("--image")],
    "schema": json.loads(Path(values("--output-schema")[0]).read_text()),
}
(log / f"{index:03d}.json").write_text(json.dumps(record))
out = Path(values("--output-last-message")[0])
if step["kind"] == "write":
    out.write_text(step["text"])
elif step["kind"] == "exit":
    sys.exit(step["code"])
elif step["kind"] == "sleep":
    time.sleep(step["seconds"])
    out.write_text(step["text"])
'''


def write(verdict: Any) -> dict[str, Any]:
    return {"kind": "write", "text": verdict if isinstance(verdict, str) else json.dumps(verdict)}


class FakeCli:
    """The fake CLI on disk: its plan (steps used in order, the last repeats) and its call records."""

    def __init__(self, root: Path) -> None:
        self.home = root / "cli"
        self.home.mkdir()
        self.script = self.home / "fake_codex.py"
        self.script.write_text(FAKE_CLI, encoding="utf-8")
        self.exchange = root / "exchange"
        self.exchange.mkdir()
        self.command = (sys.executable, "-I", str(self.script))
        self.starts = 0
        self.plan([write(GOOD_COMPARISON)])

    def plan(self, steps: list[dict[str, Any]]) -> None:
        (self.home / "plan.json").write_text(json.dumps(steps), encoding="utf-8")

    # Every start of the command, `--version` included, goes through here.
    def runner(self, argv, cwd, timeout) -> int:
        self.starts += 1
        return run_cli(argv, cwd, timeout)

    @property
    def calls(self) -> list[dict[str, Any]]:
        folder = self.home / "calls"
        if not folder.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(folder.iterdir())]

    def leftovers(self) -> list[Path]:
        return list(self.exchange.iterdir())


@pytest.fixture()
def cli(tmp_path):
    return FakeCli(tmp_path)


# Builds an opted-in provider against the fake CLI, with no real sleeping between retries.
def make_provider(cli: FakeCli, opted_in: Any = True, **overrides: Any) -> CodexProvider:
    values = {"retry_delay_seconds": 0, **overrides}
    return CodexProvider(
        CodexSettings.from_mapping(values),
        opted_in=opted_in,
        command=cli.command,
        runner=cli.runner,
        exchange_root=cli.exchange,
        sleep=lambda _: None,
    )


def _jpeg(image: Image.Image, **options: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, **options)
    return buffer.getvalue()


A_COPY = prepare_copy(_jpeg(Image.new("RGB", (640, 480), (200, 40, 40))))
B_COPY = prepare_copy(_jpeg(Image.new("RGB", (480, 640), (40, 40, 200))))


def test_compare_schema_output_is_typed(cli):
    provider = make_provider(cli)
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert result.ok and result.error is None
    assert (result.winner, result.confidence, result.reasons) == ("B", "medium", ("light", "color"))
    assert result.note == "Warmer light." and result.provider == "codex"
    assert result.attempts == 1 and result.prompt_version == COMPARE_PROMPT.version
    assert provider.status.calls == 1 and not provider.status.degraded
    [call] = cli.calls
    assert call["schema"] == COMPARISON_SCHEMA
    assert [base64.b64decode(i) for i in call["images"]] == [A_COPY, B_COPY]
    assert call["argv"][-1].startswith(COMPARE_PROMPT.text) and "image A" in call["argv"][-1]
    assert cli.leftovers() == []


def test_critique_schema_output_is_typed(cli):
    cli.plan([write(GOOD_CRITIQUE)])
    result = make_provider(cli).critique(A_COPY, CRITIQUE_PROMPT)
    assert result.ok and result.provider == "codex"
    assert result.strengths == ("clean lines",) and result.weaknesses == ("flat sky",)
    assert result.caption == "A bridge at dusk." and result.prompt_version == CRITIQUE_PROMPT.version
    [call] = cli.calls
    assert call["schema"] == CRITIQUE_SCHEMA and len(call["images"]) == 1
    assert call["argv"][-1] == CRITIQUE_PROMPT.text


def test_argv_is_ephemeral_read_only_and_schema_bound(cli):
    make_provider(cli).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    argv = cli.calls[0]["argv"]
    assert argv[0] == "exec"
    for flag in ("--ephemeral", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--output-schema") + 1] == "schema.json"
    assert argv[argv.index("--output-last-message") + 1] == "verdict.json"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="low"'
    assert "--model" not in argv


def test_configured_model_and_effort_reach_the_cli(cli):
    make_provider(cli, model="judge-model-1", reasoning_effort="medium").compare(A_COPY, B_COPY, COMPARE_PROMPT)
    argv = cli.calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "judge-model-1"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="medium"'


@pytest.mark.parametrize(
    ("step", "code"),
    [
        ({"kind": "exit", "code": 1}, "cli_failed"),
        ({"kind": "silent"}, "no_output"),
        (write("not json at all"), "invalid_output"),
        (write({"winner": "C", "confidence": "high", "reasons": [], "note": ""}), "invalid_output"),
        (write({"winner": "A", "confidence": "high", "reasons": [], "note": "", "extra": 1}), "invalid_output"),
        (write({"winner": "A"}), "invalid_output"),
        ({"kind": "sleep", "seconds": 5, "text": json.dumps(GOOD_COMPARISON)}, "timeout"),
    ],
)
def test_failure_is_bounded_recorded_and_falls_back_to_the_prescore(cli, step, code):
    cli.plan([step])
    provider = make_provider(cli, max_retries=1, timeout_seconds=0.5 if code == "timeout" else 30)
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.winner is None
    assert result.error.startswith(code)
    assert result.attempts == 2 and len(cli.calls) == 2 and provider.status.failures == 1
    assert cli.leftovers() == []
    fallback = compare_or_prescore(provider, A_COPY, B_COPY, COMPARE_PROMPT, (40.0, 75.0))
    assert fallback.ok and fallback.provider == "prescore" and fallback.winner == "B"


def test_retry_recovers_after_one_bad_verdict(cli):
    cli.plan([write("garbage"), write(GOOD_COMPARISON)])
    result = make_provider(cli, max_retries=1).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert result.ok and result.attempts == 2


def test_failure_message_never_echoes_model_text(cli):
    cli.plan([write({"winner": "SECRET-MODEL-TEXT"})])
    result = make_provider(cli, max_retries=0).compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and "SECRET-MODEL-TEXT" not in result.error


def test_missing_cli_fails_without_retry(cli, tmp_path):
    provider = CodexProvider(
        CodexSettings.from_mapping({"max_retries": 3}),
        opted_in=True,
        command=(str(tmp_path / "no-such-cli"),),
        exchange_root=cli.exchange,
        sleep=lambda _: None,
    )
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("unreachable") and result.attempts == 1
    assert not provider.health().ready
    assert cli.leftovers() == []


def test_call_cap_stops_calls_and_degrades(cli):
    provider = make_provider(cli, max_calls=2, max_retries=0)
    results = [provider.compare(A_COPY, B_COPY, COMPARE_PROMPT) for _ in range(3)]
    assert [r.ok for r in results] == [True, True, False]
    assert results[2].error.startswith("call_limit") and results[2].attempts == 0
    assert provider.status.degraded and "limit of 2" in provider.status.reason
    assert not provider.critique(A_COPY, CRITIQUE_PROMPT).ok
    assert len(cli.calls) == 2 and provider.status.calls == 2


def test_call_cap_counts_every_cli_start_including_retries(cli):
    cli.plan([write("garbage")])
    provider = make_provider(cli, max_calls=3, max_retries=5)
    result = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("call_limit") and result.attempts == 3
    assert len(cli.calls) == 3 and provider.status.degraded


def test_guard_reports_the_cap_and_the_run_goes_on_with_the_prescore(cli):
    provider = make_provider(cli, max_calls=1, max_retries=0)
    guard = GuardedProvider(provider, provider.settings.guard_settings())
    first = compare_or_prescore(guard, A_COPY, B_COPY, COMPARE_PROMPT, (80.0, 20.0))
    second = compare_or_prescore(guard, A_COPY, B_COPY, COMPARE_PROMPT, (80.0, 20.0))
    third = compare_or_prescore(guard, A_COPY, B_COPY, COMPARE_PROMPT, (20.0, 80.0))
    assert first.provider == "codex" and first.winner == "B"
    assert (second.provider, second.winner) == ("prescore", "A")
    assert (third.provider, third.winner) == ("prescore", "B")
    assert guard.status.degraded and "limit of 1" in guard.status.reason
    assert len(cli.calls) == 1


def test_guard_adopts_a_cap_reached_inside_one_call(cli):
    cli.plan([write("garbage")])
    provider = make_provider(cli, max_calls=1, max_retries=2)
    guard = GuardedProvider(provider, provider.settings.guard_settings())
    result = guard.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    assert not result.ok and result.error.startswith("call_limit")
    # One failure is below the breaker of 3; the guard is degraded because the provider is.
    assert guard.status.degraded and "1 Codex calls" in guard.status.reason
    assert guard.compare(A_COPY, B_COPY, COMPARE_PROMPT).error.startswith("degraded")
    assert len(cli.calls) == 1


@pytest.mark.parametrize("opted_in", [False, None, "yes", 1])
def test_without_opt_in_no_call_and_no_file(cli, opted_in):
    provider = make_provider(cli, opted_in=opted_in)
    compared = provider.compare(A_COPY, B_COPY, COMPARE_PROMPT)
    critiqued = provider.critique(A_COPY, CRITIQUE_PROMPT)
    health = provider.health()
    assert not compared.ok and compared.error.startswith("opt_in_required") and compared.attempts == 0
    assert not critiqued.ok and critiqued.error.startswith("opt_in_required")
    assert not health.ready and not health.reachable and "opt-in" in health.message
    assert cli.starts == 0 and cli.calls == [] and cli.leftovers() == []
    assert provider.status.calls == 0
    fallback = compare_or_prescore(provider, A_COPY, B_COPY, COMPARE_PROMPT, (90.0, 10.0))
    assert fallback.provider == "prescore" and fallback.winner == "A"


def test_health_with_opt_in_starts_only_the_version_check(cli):
    health = make_provider(cli).health()
    assert health.ready and cli.starts == 1 and cli.calls == [] and cli.leftovers() == []


def test_what_reaches_the_cli_is_anonymous(cli, tmp_path, monkeypatch):
    photos = tmp_path / "photos-secret-trip"
    photos.mkdir()
    source = photos / "secret_holiday_name.jpg"
    exif = Image.Exif()
    exif[0x010E] = "secret-description"
    exif[0x013B] = "secret-artist"
    Image.effect_noise((2400, 1600), 40).convert("RGB").save(
        source, "JPEG", quality=95, exif=exif.tobytes(), comment=b"secret-comment",
        icc_profile=b"secret-icc-profile",
    )
    original = source.read_bytes()
    assert b"secret-description" in original and b"secret-icc-profile" in original
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-not-for-the-cli")
    monkeypatch.setenv("AURAFRAME_PHOTOS_DIR", str(photos))

    first, second = prepare_copy(source), prepare_copy(original)
    assert make_provider(cli).compare(first, second, COMPARE_PROMPT).ok

    [call] = cli.calls
    sent = [base64.b64decode(i) for i in call["images"]]
    assert sent == [first, second]
    for data in sent:
        check_copy(data)
        with Image.open(io.BytesIO(data)) as image:
            assert len(image.getexif()) == 0
            assert not ({"exif", "icc_profile", "comment"} & set(image.info))
        for secret in (b"Exif", b"ICC_PROFILE", b"secret", b"holiday", photos.name.encode()):
            assert secret not in data
    assert call["argv"][call["argv"].index("--image") + 1] == "a.jpg"
    assert [a for i, a in enumerate(call["argv"]) if i and call["argv"][i - 1] == "--image"] == ["a.jpg", "b.jpg"]
    assert call["listing"] == ["a.jpg", "b.jpg", "schema.json"]
    for text in [*call["argv"], call["cwd"]]:
        assert "secret" not in text and "holiday" not in text and str(photos) not in text
    # The child interpreter may add LC_CTYPE itself when it coerces a C locale (PEP 538).
    allowed = {"PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "SYSTEMROOT", "LC_CTYPE"}
    assert set(call["env"]) <= allowed
    assert not any("secret" in value or str(photos) in value for value in call["env"].values())
    assert Path(call["cwd"]).parent == cli.exchange.resolve() and Path(call["cwd"]).name.startswith("call-")
    assert cli.leftovers() == [] and source.read_bytes() == original


def test_unprepared_original_never_reaches_the_cli(cli, tmp_path):
    big = _jpeg(Image.new("RGB", (3000, 2000), "white"))
    tagged_exif = Image.Exif()
    tagged_exif[0x010E] = "a description"
    tagged = _jpeg(Image.new("RGB", (64, 64)), exif=tagged_exif.tobytes())
    png = io.BytesIO()
    Image.new("RGB", (64, 64)).save(png, format="PNG")
    provider = make_provider(cli)
    for bad in (big, tagged, png.getvalue(), b"not an image"):
        result = provider.compare(bad, B_COPY, COMPARE_PROMPT)
        assert not result.ok and result.error.startswith("invalid_input") and result.attempts == 0
    assert not provider.critique(tagged, CRITIQUE_PROMPT).ok
    assert cli.starts == 0 and cli.leftovers() == [] and provider.status.calls == 0


def test_settings_defaults_bounds_and_names():
    defaults = CodexSettings.from_mapping({})
    assert defaults.max_calls == 200 and defaults.reasoning_effort == "low" and defaults.model == ""
    huge = CodexSettings.from_mapping({"max_calls": 10**9, "max_retries": 99, "timeout_seconds": 10**6})
    assert huge.max_calls == CALL_LIMIT_BOUNDS[1] and huge.max_retries == RETRY_BOUNDS[1]
    assert huge.timeout_seconds == TIMEOUT_BOUNDS[1]
    tiny = CodexSettings.from_mapping({"max_calls": 0, "timeout_seconds": 0})
    assert tiny.max_calls == 1 and tiny.timeout_seconds == TIMEOUT_BOUNDS[0]
    assert CodexSettings.from_mapping({"max_calls": "many"}).max_calls == 200
    for bad in ("-rf", "--sandbox", "a b", "name;rm", "x" * 80):
        with pytest.raises(ValueError):
            CodexSettings.from_mapping({"model": bad})
    with pytest.raises(ValueError):
        CodexSettings.from_mapping({"reasoning_effort": "extreme"})
    assert not hasattr(defaults, "opted_in")
    guard = defaults.guard_settings()
    assert guard.max_calls == 200 and guard.max_consecutive_failures == 3


def test_engine_config_ai_codex_loads_without_an_opt_in_key():
    raw = load_config()["ai"]["codex"]
    assert not any("opt" in key for key in raw)
    settings = CodexSettings.from_mapping(raw)
    assert settings.max_calls == 200 and settings.reasoning_effort == "low"
