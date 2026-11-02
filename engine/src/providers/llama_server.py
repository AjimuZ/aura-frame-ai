"""VisionProvider for the local llama-server (OpenAI-compatible chat endpoint, images as data URLs).

Every call is bounded: a per-attempt timeout, a bounded number of retries, and a response checked
against the verdict JSON Schema. A call that cannot be completed comes back as a failed result,
never as an exception, so the run goes on.
"""

import base64
import json
import logging
import time
from typing import Any, Callable

import httpx

from .contract import (
    Comparison,
    Critique,
    Prompt,
    ProviderHealth,
    ProviderSettings,
)
from .images import check_copy
from .schemas import COMPARISON_SCHEMA, CRITIQUE_SCHEMA, validate

PROVIDER_NAME = "llama-server"

_LOG = logging.getLogger(__name__)

# HTTP statuses that a later attempt can fix; every other 4xx is final.
_RETRYABLE_STATUS = {408, 425, 429}


class _CallFailed(Exception):
    """One attempt failed. `retryable` tells whether another attempt may succeed."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LlamaServerProvider:
    """Talks to llama-server's `/v1/chat/completions` with a schema-constrained response."""

    name = PROVIDER_NAME

    # Stores the settings; `transport` and `sleep` are injectable for tests.
    def __init__(
        self,
        settings: ProviderSettings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        # trust_env=False keeps proxy variables out of a local call, and a redirect is never followed.
        self._client = httpx.Client(
            base_url=settings.endpoint,
            timeout=httpx.Timeout(settings.timeout_seconds),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    # Releases the connection pool.
    def close(self) -> None:
        self._client.close()

    # Probes /health and /v1/models without running inference; the message says what to do next.
    def health(self) -> ProviderHealth:
        try:
            status = self._client.get("/health", timeout=5.0)
        except httpx.HTTPError:
            return ProviderHealth(
                False, False,
                f"The vision service is not reachable at {self.settings.endpoint}. "
                "Start the stack with ./start.sh, with the local provider.",
            )
        if status.status_code != 200:
            return ProviderHealth(
                True, False,
                f"The vision service answered {status.status_code}; the model may still be loading.",
            )
        try:
            listing = self._client.get("/v1/models", timeout=5.0)
            ids = [str(item.get("id", "")) for item in listing.json().get("data", [])]
        except (httpx.HTTPError, ValueError, AttributeError):
            ids = []
        if not self._model_listed(ids):
            wanted = self.settings.model or "any model"
            return ProviderHealth(
                True, False,
                f"The vision service has no model loaded for '{wanted}'. "
                "Check that the model files are in the models volume (see docs/operations.md).",
            )
        return ProviderHealth(True, True, "ok")

    # Tells whether the configured model is among the loaded ones; with none configured, any is fine.
    def _model_listed(self, ids: list[str]) -> bool:
        if not ids:
            return False
        wanted = self.settings.model
        return not wanted or any(wanted == i or wanted == i.replace("\\", "/").rsplit("/", 1)[-1] for i in ids)

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

    # Runs the bounded attempt loop. Returns (verdict, attempts used, error); the error is None on success.
    def _judge(
        self, images: list[bytes], prompt: Prompt, schema: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, int, str | None]:
        try:
            for image in images:
                check_copy(image)
        except ValueError as exc:
            return None, 0, f"invalid_input: {exc}"
        body = self._request_body(images, prompt, schema)
        attempts = 0
        error = "unreachable: no attempt was made"
        for attempt in range(self.settings.max_retries + 1):
            if attempt:
                self._sleep(self.settings.retry_delay_seconds * attempt)
            attempts += 1
            try:
                return self._attempt(body, schema), attempts, None
            except _CallFailed as exc:
                error = f"{exc.code}: {exc}"
                _LOG.warning("Vision call failed (attempt %d): %s", attempts, exc.code)
                if not exc.retryable:
                    break
        return None, attempts, error

    # Builds the chat request. Each image is labelled, so the prompt can speak of "image A" and "image B".
    def _request_body(self, images: list[bytes], prompt: Prompt, schema: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt.text}]
        for index, image in enumerate(images):
            if len(images) > 1:
                content.append({"type": "text", "text": f"Image {'AB'[index]}:"})
            url = "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url}})
        return {
            "model": self.settings.model or "local",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": prompt.name, "strict": True, "schema": schema},
            },
        }

    # Makes one request and returns a validated verdict, or raises _CallFailed with a short code.
    def _attempt(self, body: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post("/v1/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise _CallFailed("timeout", "The vision service did not answer in time.", True) from exc
        except httpx.HTTPError as exc:
            raise _CallFailed("unreachable", "The vision service is not reachable.", True) from exc
        status = response.status_code
        if status == 404:
            raise _CallFailed("model_missing", "The vision service has no matching model.", False)
        if status >= 500 or status in _RETRYABLE_STATUS:
            raise _CallFailed(f"http_{status}", "The vision service reported a temporary error.", True)
        if status != 200:
            raise _CallFailed(f"http_{status}", "The vision service refused the request.", False)
        try:
            text = response.json()["choices"][0]["message"]["content"]
            verdict = json.loads(text)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _CallFailed("invalid_output", "The response was not usable JSON.", True) from exc
        problems = validate(verdict, schema)
        if problems:
            raise _CallFailed("invalid_output", f"The response broke the schema ({problems[0]}).", True)
        return verdict
