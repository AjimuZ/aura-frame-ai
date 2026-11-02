"""Transport from the `api` container to the opt-in Codex sidecar: one authenticated HTTP request per CLI start.

`api` has no CLI and no Docker socket. `SidecarRunner` fills the `Runner` contract of `codex.py`: it sends
the CLI arguments without the command name, and only the files `a.jpg`, `b.jpg` and `schema.json` of the
call folder, to the sidecar's `POST /v1/run` on the internal `backend` network. The sidecar runs the CLI
in its own private folder and returns the exit code and the verdict text, which is written back as
`verdict.json` in the call folder. The shared secret is `AURAFRAME_CODEX_TOKEN`, generated per start by
start.sh; it travels only in the Authorization header. Contract of the other side:
docker/codex/sidecar-server.cjs (ADR 0018).
"""

import base64
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx

from .codex import CodexProvider, CodexSettings

# Environment variable that carries the per-start secret shared by `api` and the sidecar.
TOKEN_ENV = "AURAFRAME_CODEX_TOKEN"
MIN_TOKEN_LENGTH = 32

# The only files of a call folder that are ever sent; anything else in the folder stays in `api`.
_SENT_FILES = ("a.jpg", "b.jpg", "schema.json")
_VERDICT_FILE = "verdict.json"
# A verdict is a few hundred bytes; a larger answer is dropped, which the provider reports as `no_output`.
_MAX_VERDICT_BYTES = 65536
# Extra seconds the request waits beyond the CLI timeout, so the sidecar can report its own timeout first.
_MARGIN_SECONDS = 15.0


class SidecarRunner:
    """A `Runner` that starts the CLI in the sidecar. Callable as `runner(argv, cwd, timeout) -> exit code`.

    Raises subprocess.TimeoutExpired when the CLI or the request timed out, and OSError when the sidecar
    is unreachable, refuses the call, could not start the CLI or answers in an unknown shape.
    """

    # Requires an http(s) endpoint and a token of at least 32 characters, else raises ValueError.
    # `transport` is injectable for tests.
    def __init__(self, endpoint: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("The Codex sidecar endpoint must start with http:// or https://.")
        if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"{TOKEN_ENV} is missing or shorter than {MIN_TOKEN_LENGTH} characters.")
        self._headers = {"Authorization": f"Bearer {token}"}
        # trust_env=False keeps proxy variables out of an internal call, and a redirect is never followed.
        self._client = httpx.Client(
            base_url=endpoint, trust_env=False, follow_redirects=False, transport=transport
        )

    # Builds a runner from the token in `environ` (the process environment by default); raises ValueError
    # when the token is missing, which means the stack was not started with `--provider codex`.
    @classmethod
    def from_environment(
        cls,
        endpoint: str,
        environ: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> "SidecarRunner":
        source = os.environ if environ is None else environ
        return cls(endpoint, source.get(TOKEN_ENV, ""), transport=transport)

    # Releases the connection pool.
    def close(self) -> None:
        self._client.close()

    # Hands one CLI start to the sidecar and returns its exit code. `argv[0]` is the command name, which
    # the sidecar fixes itself, so it is never sent; the timeout bounds the CLI there.
    def __call__(self, argv: Sequence[str], cwd: Path, timeout: float) -> int:
        files = {
            name: base64.b64encode((cwd / name).read_bytes()).decode("ascii")
            for name in _SENT_FILES
            if (cwd / name).is_file()
        }
        body = {"args": list(argv[1:]), "files": files, "timeout_seconds": timeout}
        try:
            response = self._client.post(
                "/v1/run", json=body, headers=self._headers, timeout=timeout + _MARGIN_SECONDS
            )
        except httpx.TimeoutException as exc:
            raise subprocess.TimeoutExpired(list(argv[:1]), timeout) from exc
        except httpx.HTTPError as exc:
            raise OSError("The Codex sidecar is not reachable.") from exc
        if response.status_code != 200:
            raise OSError(f"The Codex sidecar refused the call (HTTP {response.status_code}).")
        code, timed_out, verdict = _read_result(response)
        if timed_out:
            raise subprocess.TimeoutExpired(list(argv[:1]), timeout)
        if verdict is not None and len(verdict.encode("utf-8")) <= _MAX_VERDICT_BYTES:
            (cwd / _VERDICT_FILE).write_text(verdict, encoding="utf-8")
        return code


# Reads (exit code, timed out, verdict text or None) from a 200 answer; raises OSError for any other shape.
def _read_result(response: httpx.Response) -> tuple[int, bool, str | None]:
    try:
        result: Any = response.json()
    except ValueError as exc:
        raise OSError("The Codex sidecar answered with something other than JSON.") from exc
    if not isinstance(result, dict):
        raise OSError("The Codex sidecar answered in an unknown shape.")
    code = result.get("exit_code")
    timed_out = result.get("timed_out")
    verdict = result.get("verdict")
    if (
        not isinstance(code, int)
        or isinstance(code, bool)
        or not isinstance(timed_out, bool)
        or not (verdict is None or isinstance(verdict, str))
    ):
        raise OSError("The Codex sidecar answered in an unknown shape.")
    return code, timed_out, verdict


# Builds the Codex provider that judges through the sidecar. `opted_in` must be True only after
# `--provider codex` and the per-library confirmation. Raises ValueError when the token is missing.
# `transport` is injectable for tests.
def sidecar_provider(
    settings: CodexSettings,
    *,
    opted_in: bool,
    environ: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    transport: httpx.BaseTransport | None = None,
) -> CodexProvider:
    runner = SidecarRunner.from_environment(settings.endpoint, environ, transport)
    return CodexProvider(settings, opted_in=opted_in, command=("codex",), runner=runner, sleep=sleep)
