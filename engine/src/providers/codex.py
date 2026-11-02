"""VisionProvider for the opt-in Codex CLI judge: one `codex exec` call per verdict, gated, capped and bounded.

Nothing happens without opt-in: no file is written and no process starts until the caller passes
`opted_in=True`, which it may do only after `--provider codex` and the per-library confirmation. Each
call writes the anonymous copies under neutral names (`a.jpg`, `b.jpg`) in a fresh private folder,
runs the CLI with `shell=False`, reads the verdict file the CLI writes, validates it against the
verdict schema, and removes the folder whatever happened. Every CLI start counts against
`max_calls`; at the cap the provider refuses further calls and reports `degraded`, so the run goes
on with the local prescore. No method raises.

The command and the runner are injectable. In the stack, `api` has no CLI of its own and uses the
`SidecarRunner` of `codex_sidecar.py`, which hands each start to the sidecar over the internal network.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .contract import (
    CALL_LIMIT_BOUNDS,
    FAILURE_LIMIT_BOUNDS,
    RETRY_BOUNDS,
    RETRY_DELAY_BOUNDS,
    TIMEOUT_BOUNDS,
    Comparison,
    Critique,
    Prompt,
    ProviderHealth,
    ProviderSettings,
    ProviderStatus,
    _bounded,
)
from .images import check_copy
from .schemas import COMPARISON_SCHEMA, CRITIQUE_SCHEMA, validate

PROVIDER_NAME = "codex"

# Efforts the CLI accepts for `model_reasoning_effort`; the short judging task needs `low`.
REASONING_EFFORTS = ("minimal", "low", "medium", "high")

# A model name is passed as one argv value, so it must not look like a flag or carry odd characters.
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")

# The only environment variables the CLI inherits. No key, token or photo setting is passed on.
_ENV_KEYS = ("PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "SYSTEMROOT")

# Neutral names of the copies in the call folder, in the order the prompt calls them A and B.
_IMAGE_NAMES = ("a.jpg", "b.jpg")

# Tells the CLI which attachment is which; part of the request of this provider only.
_ORDER_NOTE = "\n\nThe first attached image is image A and the second attached image is image B."

_LOG = logging.getLogger(__name__)

# Runs one CLI command in `cwd` within `timeout` seconds and returns its exit code. It must raise
# subprocess.TimeoutExpired on a timeout and OSError when the command cannot start.
Runner = Callable[[Sequence[str], Path, float], int]


# Starts the CLI without a shell, with a minimal environment and no input, and discards its output so
# no model text or image data can reach a log. The child is killed when the timeout expires.
def run_cli(argv: Sequence[str], cwd: Path, timeout: float) -> int:
    env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False
        list(argv),
        cwd=cwd,
        env=env,
        timeout=timeout,
        shell=False,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode


@dataclass(frozen=True)
class CodexSettings:
    """Model choice and bounded limits for the Codex provider (`ai.codex` in the engine configuration).

    Opt-in is deliberately not a setting: a configuration file can never turn the provider on.
    Always build it with `from_mapping`, which clamps every limit and validates the names.
    `endpoint` is the sidecar's HTTP service on the internal `backend` network.
    """

    endpoint: str = "http://codex:8090"
    model: str = ""
    reasoning_effort: str = "low"
    timeout_seconds: float = 180.0
    max_retries: int = 1
    retry_delay_seconds: float = 2.0
    max_calls: int = 200
    max_consecutive_failures: int = 3

    # Builds settings from the `ai.codex` mapping. Unknown keys are ignored, numbers are clamped, and an
    # endpoint that is not http(s), or a model name or effort the CLI could misread, raises ValueError.
    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "CodexSettings":
        raw = values or {}
        defaults = cls()
        endpoint = str(raw.get("endpoint") or defaults.endpoint).strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("The Codex sidecar endpoint must start with http:// or https://.")
        model = str(raw.get("model") or "").strip()
        if model and not _MODEL_NAME.fullmatch(model):
            raise ValueError("The Codex model name may hold only letters, digits, '.', '_', ':' and '-'.")
        effort = str(raw.get("reasoning_effort") or defaults.reasoning_effort).strip()
        if effort not in REASONING_EFFORTS:
            raise ValueError(f"The Codex reasoning effort must be one of {', '.join(REASONING_EFFORTS)}.")
        return cls(
            endpoint=endpoint,
            model=model,
            reasoning_effort=effort,
            timeout_seconds=_bounded(
                raw.get("timeout_seconds"), defaults.timeout_seconds, TIMEOUT_BOUNDS, float
            ),
            max_retries=_bounded(raw.get("max_retries"), defaults.max_retries, RETRY_BOUNDS, int),
            retry_delay_seconds=_bounded(
                raw.get("retry_delay_seconds"), defaults.retry_delay_seconds, RETRY_DELAY_BOUNDS, float
            ),
            max_calls=_bounded(raw.get("max_calls"), defaults.max_calls, CALL_LIMIT_BOUNDS, int),
            max_consecutive_failures=_bounded(
                raw.get("max_consecutive_failures"),
                defaults.max_consecutive_failures,
                FAILURE_LIMIT_BOUNDS,
                int,
            ),
        )

    # Limits for a GuardedProvider around this provider. The guard counts verdicts, which are never more
    # than CLI starts, so its call limit trips no earlier than the provider's own cap; it adds the
    # breaker for failures in a row.
    def guard_settings(self) -> ProviderSettings:
        return ProviderSettings(
            model=self.model,
            max_calls=self.max_calls,
            max_consecutive_failures=self.max_consecutive_failures,
        )


class _CallFailed(Exception):
    """One CLI start failed. `retryable` tells whether another start may succeed."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class CodexProvider:
    """Judges prepared copies with `codex exec`, only after opt-in and within a per-run call cap.

    `status.calls` counts CLI starts, retries included, because each start is a billed agent turn.
    """

    name = PROVIDER_NAME

    # Stores the settings. Without `opted_in=True` every call fails with `opt_in_required` before any
    # file or process is created. `command` is the CLI (default `codex`), `runner` starts it,
    # `exchange_root` holds the per-call folders (the system temporary folder when None).
    def __init__(
        self,
        settings: CodexSettings,
        *,
        opted_in: bool = False,
        command: Sequence[str] = ("codex",),
        runner: Runner = run_cli,
        exchange_root: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.opted_in = opted_in is True
        self._command = tuple(command)
        self._runner = runner
        self._exchange_root = exchange_root
        self._sleep = sleep
        self.status = ProviderStatus()

    # Reports opt-in first; with opt-in, checks only that the CLI starts (`--version`), which costs no
    # call. Sign-in and the model are not checked here: a failed call reports them. Never raises.
    def health(self) -> ProviderHealth:
        if not self.opted_in:
            return ProviderHealth(
                False, False,
                "The Codex provider is off. It needs --provider codex and opt-in for this library, "
                "because resized copies of the photos would leave the computer.",
            )
        try:
            with tempfile.TemporaryDirectory(prefix="call-", dir=self._exchange_root) as folder:
                code = self._runner([*self._command, "--version"], Path(folder), 10.0)
        except (OSError, subprocess.SubprocessError):
            code = -1
        if code != 0:
            return ProviderHealth(
                False, False,
                "The Codex CLI did not start. Check that the sidecar runs (see docs/ai-providers.md).",
            )
        return ProviderHealth(True, True, "ok; sign-in and the model are checked by the first call")

    # Judges two prepared copies in the order given, returning a failed Comparison on any problem.
    def compare(self, first: bytes, second: bytes, prompt: Prompt) -> Comparison:
        verdict, attempts, error = self._judge([first, second], prompt, COMPARISON_SCHEMA)
        meta = dict(
            provider=PROVIDER_NAME, model=self.settings.model,
            prompt_version=prompt.version, attempts=attempts,
        )
        if verdict is None:
            return Comparison(ok=False, error=error, **meta)
        return Comparison(
            ok=True,
            winner=verdict["winner"],
            confidence=verdict["confidence"],
            reasons=tuple(verdict["reasons"]),
            note=verdict["note"],
            **meta,
        )

    # Critiques one prepared copy, returning a failed Critique on any problem.
    def critique(self, image: bytes, prompt: Prompt) -> Critique:
        verdict, attempts, error = self._judge([image], prompt, CRITIQUE_SCHEMA)
        meta = dict(
            provider=PROVIDER_NAME, model=self.settings.model,
            prompt_version=prompt.version, attempts=attempts,
        )
        if verdict is None:
            return Critique(ok=False, error=error, **meta)
        return Critique(
            ok=True,
            strengths=tuple(verdict["strengths"]),
            weaknesses=tuple(verdict["weaknesses"]),
            caption=verdict["caption"],
            **meta,
        )

    # Marks the provider as degraded once; the first reason is the one the UI shows.
    def _degrade(self, reason: str) -> None:
        if not self.status.degraded:
            self.status.degraded = True
            self.status.reason = reason
            _LOG.warning("Codex provider degraded to the prescore: %s", reason)

    # Reserves one CLI start. Returns an error code when no start is allowed, else None.
    def _admit(self) -> str | None:
        if self.status.degraded:
            return f"degraded: {self.status.reason}"
        if self.status.calls >= self.settings.max_calls:
            self._degrade(f"The limit of {self.settings.max_calls} Codex calls was reached.")
            return f"call_limit: {self.status.reason}"
        self.status.calls += 1
        return None

    # Runs the gated, bounded attempt loop. Returns (verdict, CLI starts, error); the error is None on
    # success. The opt-in and the copies are checked before anything is written or started.
    def _judge(
        self, images: list[bytes], prompt: Prompt, schema: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, int, str | None]:
        if not self.opted_in:
            return None, 0, "opt_in_required: the Codex provider needs opt-in for this library"
        try:
            for image in images:
                check_copy(image)
        except ValueError as exc:
            return None, 0, f"invalid_input: {exc}"
        attempts = 0
        error = "unreachable: no attempt was made"
        for attempt in range(self.settings.max_retries + 1):
            refused = self._admit()
            if refused:
                error = refused
                break
            if attempt:
                self._sleep(self.settings.retry_delay_seconds * attempt)
            attempts += 1
            try:
                return self._attempt(images, prompt, schema), attempts, None
            except _CallFailed as exc:
                error = f"{exc.code}: {exc}"
                _LOG.warning("Codex call failed (attempt %d): %s", attempts, exc.code)
                if not exc.retryable:
                    break
        self.status.failures += 1
        return None, attempts, error

    # Builds the argv for one call. Paths are relative to the call folder, so no folder name of the
    # engine reaches the CLI, and the prompt is the last argument.
    def _argv(self, image_names: Sequence[str], prompt: Prompt) -> list[str]:
        argv = [
            *self._command, "exec",
            "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
            "-c", f'model_reasoning_effort="{self.settings.reasoning_effort}"',
        ]
        if self.settings.model:
            argv += ["--model", self.settings.model]
        for name in image_names:
            argv += ["--image", name]
        argv += ["--output-schema", "schema.json", "--output-last-message", "verdict.json"]
        text = prompt.text + (_ORDER_NOTE if len(image_names) > 1 else "")
        return [*argv, text]

    # Makes one CLI start in a fresh private folder that is removed afterwards, even on failure, and
    # returns the validated verdict, or raises _CallFailed with a short code and no model text.
    def _attempt(self, images: list[bytes], prompt: Prompt, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            with tempfile.TemporaryDirectory(prefix="call-", dir=self._exchange_root) as name:
                folder = Path(name)
                names = _IMAGE_NAMES[: len(images)]
                for file_name, image in zip(names, images):
                    (folder / file_name).write_bytes(image)
                (folder / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
                try:
                    code = self._runner(self._argv(names, prompt), folder, self.settings.timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    raise _CallFailed("timeout", "The Codex CLI did not finish in time.", True) from exc
                except OSError as exc:
                    raise _CallFailed("unreachable", "The Codex CLI could not be started.", False) from exc
                if code != 0:
                    raise _CallFailed("cli_failed", f"The Codex CLI exited with code {code}.", True)
                try:
                    text = (folder / "verdict.json").read_text(encoding="utf-8")
                except OSError as exc:
                    raise _CallFailed("no_output", "The Codex CLI wrote no verdict.", True) from exc
        except OSError as exc:
            raise _CallFailed("exchange_failed", "The call folder could not be prepared.", False) from exc
        try:
            verdict = json.loads(text)
        except ValueError as exc:
            raise _CallFailed("invalid_output", "The verdict was not usable JSON.", True) from exc
        problems = validate(verdict, schema)
        if problems:
            raise _CallFailed("invalid_output", f"The verdict broke the schema ({problems[0]}).", True)
        return verdict
