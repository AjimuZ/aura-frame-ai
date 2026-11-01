"""Minimal OpenAI REST client for Quality Mode (via `requests`, no SDK dependency).

Only resized, EXIF-stripped image copies are ever sent (the caller prepares them). The API
key is read from the environment and never logged. Errors are classified as RetryableError
(429 / timeout / 5xx — the rate limiter retries) or FatalApiError (auth / bad request /
model missing). Per-stage model resolution falls back to the configured fallback model when
the primary is not available on the account.
"""

import os
from typing import Any

import requests

from .. import progress_events as events

DEFAULT_BASE_URL = "https://api.openai.com"


class RetryableError(Exception):
    """A transient failure worth retrying (rate limit, timeout, server error)."""

    # Stores an optional Retry-After hint (seconds) parsed from response headers.
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class FatalApiError(Exception):
    """A non-retryable failure (auth, malformed request, refusal)."""


class ModelNotAvailableError(FatalApiError):
    """The requested model (and its fallback) is not available on the account."""


class MissingApiKeyError(FatalApiError):
    """No API key was found in the configured environment variable."""


# Extracts a human-readable error message from an error response without leaking secrets.
def _error_message(response: requests.Response) -> str:
    try:
        body = response.json()
        return str(body.get("error", {}).get("message", body))[:300]
    except Exception:  # noqa: BLE001 - error bodies are sometimes not JSON
        return (response.text or "")[:300]


# Parses the Retry-After header (seconds) if present.
def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class OpenAiClient:
    """Talks to the OpenAI Chat Completions endpoint for vision review."""

    # Reads key, base URL, and timeout from config/environment.
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        oc = config.get("openai", {})
        self._key = os.environ.get(oc.get("api_key_env", "OPENAI_API_KEY"), "").strip()
        self._base = str(oc.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = int(oc.get("concurrency", {}).get("request_timeout_seconds", 90))
        self._models_cache: set[str] | None = None

    # True when an API key is configured in the environment.
    @property
    def has_key(self) -> bool:
        return bool(self._key)

    # Builds auth headers, raising if no key is configured.
    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise MissingApiKeyError(
                "OPENAI_API_KEY is not set. Configure it in the environment or app settings."
            )
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    # Returns the set of model ids available on the account (cached). Empty set if listing
    # failed for a non-auth reason (callers then optimistically try the primary model).
    def available_models(self) -> set[str]:
        if self._models_cache is not None:
            return self._models_cache
        try:
            response = requests.get(f"{self._base}/v1/models", headers=self._headers(), timeout=15)
            response.raise_for_status()
            ids = {str(m.get("id", "")) for m in response.json().get("data", [])}
        except MissingApiKeyError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade gracefully if listing is unavailable
            events.log(f"Could not list OpenAI models: {exc}", "warning")
            ids = set()
        self._models_cache = ids
        return ids

    # Resolves the model to use for a stage: primary if available, else the configured
    # fallback, else raises ModelNotAvailableError.
    def resolve_model(self, stage: str) -> str:
        oc = self._config.get("openai", {})
        primary = str(oc.get("models", {}).get(stage, ""))
        fallback = str(oc.get("fallback_models", {}).get(stage, ""))
        models = self.available_models()
        if not models:
            return primary or fallback
        if primary and primary in models:
            return primary
        if fallback and fallback in models:
            events.log(
                f"OpenAI model '{primary}' unavailable for {stage}; using fallback '{fallback}'.",
                "warning",
            )
            return fallback
        raise ModelNotAvailableError(
            f"Neither '{primary}' nor '{fallback}' is available for stage '{stage}'."
        )

    # Sends a single-image review request and returns the raw response text.
    def review_image(
        self, *, model: str, prompt: str, image_data_url: str, detail: str, max_output_tokens: int
    ) -> str:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_url, "detail": detail}},
        ]
        return self._post_chat(self._payload(model, content, max_output_tokens))

    # Sends a multi-image comparative request (Final Judge) and returns the raw response text.
    def review_many(
        self, *, model: str, prompt: str, image_data_urls: list[str], detail: str, max_output_tokens: int
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url, "detail": detail}})
        return self._post_chat(self._payload(model, content, max_output_tokens))

    # Builds the chat-completions request body.
    def _payload(self, model: str, content: list[dict[str, Any]], max_output_tokens: int) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": int(max_output_tokens),
            "temperature": 0.2,
        }

    # POSTs a chat-completions request, classifying failures and adapting once to parameter
    # incompatibilities (some models reject max_tokens/temperature/response_format names).
    def _post_chat(self, payload: dict[str, Any]) -> str:
        for attempt in (1, 2):
            try:
                response = requests.post(
                    f"{self._base}/v1/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self._timeout,
                )
            except requests.Timeout as exc:
                raise RetryableError(f"request timed out: {exc}") from exc
            except requests.RequestException as exc:
                raise RetryableError(f"connection error: {exc}") from exc

            if response.status_code == 200:
                choices = response.json().get("choices", [{}])
                return choices[0].get("message", {}).get("content", "") or ""
            if response.status_code == 429:
                raise RetryableError("rate limited (429)", retry_after=_retry_after(response))
            if response.status_code >= 500:
                raise RetryableError(f"server error {response.status_code}")
            if response.status_code == 400 and attempt == 1 and self._adapt_payload(payload, response):
                continue
            if response.status_code in (401, 403):
                raise FatalApiError(f"auth error {response.status_code}: {_error_message(response)}")
            if response.status_code == 404:
                raise ModelNotAvailableError(_error_message(response))
            raise FatalApiError(f"OpenAI error {response.status_code}: {_error_message(response)}")
        raise FatalApiError("OpenAI request failed after parameter adaptation")

    # Mutates the payload to work around a 400 about an unsupported parameter; returns True
    # if it changed anything (so the request is retried once).
    def _adapt_payload(self, payload: dict[str, Any], response: requests.Response) -> bool:
        message = _error_message(response).lower()
        changed = False
        if "max_completion_tokens" in message and "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
            changed = True
        elif "max_tokens" in message and "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
            changed = True
        if "temperature" in message and "temperature" in payload:
            payload.pop("temperature", None)
            changed = True
        if "response_format" in message and "response_format" in payload:
            payload.pop("response_format", None)
            changed = True
        return changed

    # Returns a privacy-safe status report (key present, reachable, per-stage resolved model).
    def check(self) -> dict[str, Any]:
        report: dict[str, Any] = {"hasKey": self.has_key, "reachable": False, "models": {}}
        if not self.has_key:
            report["message"] = "OPENAI_API_KEY is not set."
            return report
        models = self.available_models()
        report["reachable"] = bool(models)
        for stage in ("broad_scan", "quality_review", "premium_review", "final_judge"):
            try:
                report["models"][stage] = self.resolve_model(stage)
            except ModelNotAvailableError as exc:
                report["models"][stage] = None
                report.setdefault("warnings", []).append(str(exc))
        report["message"] = "ok" if report["reachable"] else "Could not reach the OpenAI API."
        return report
