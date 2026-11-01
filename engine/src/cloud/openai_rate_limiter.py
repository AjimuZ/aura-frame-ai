"""Concurrency + retry control for OpenAI Quality Mode.

Caps the number of parallel requests, retries transient failures (429 / timeouts / 5xx)
with exponential backoff (honoring Retry-After when present), and never loses results that
already completed: a request that ultimately fails yields None for that item rather than
aborting the batch.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .. import progress_events as events
from .openai_client import RetryableError


class RateLimiter:
    """Runs request workers with bounded parallelism and bounded retries."""

    # Reads concurrency settings (parallelism, retry attempts, backoff) from config.
    def __init__(self, config: dict[str, Any]) -> None:
        conc = config.get("openai", {}).get("concurrency", {})
        self.max_parallel = max(1, int(conc.get("max_parallel_requests", 4)))
        self.attempts = max(0, int(conc.get("retry_attempts", 2)))
        self.backoff = float(conc.get("retry_backoff_seconds", 3))
        self.respect_headers = bool(conc.get("respect_rate_limit_headers", True))

    # Calls fn(), retrying on RetryableError with exponential backoff (capped). Re-raises the
    # last error if all attempts are exhausted; non-retryable errors propagate immediately.
    def call_with_retries(self, fn: Callable[[], Any]) -> Any:
        last: RetryableError | None = None
        for attempt in range(self.attempts + 1):
            try:
                return fn()
            except RetryableError as exc:
                last = exc
                if attempt >= self.attempts:
                    break
                delay = exc.retry_after if (self.respect_headers and exc.retry_after) else self.backoff * (2 ** attempt)
                time.sleep(min(float(delay), 30.0))
        assert last is not None
        raise last

    # Runs `worker(item)` for each item with bounded concurrency + retries. Returns a list
    # aligned with `items`; entries are None where the request ultimately failed.
    def run(self, items: list[Any], worker: Callable[[Any], Any]) -> list[Any]:
        results: list[Any] = [None] * len(items)

        def task(index: int, item: Any) -> tuple[int, Any]:
            try:
                return index, self.call_with_retries(lambda: worker(item))
            except Exception as exc:  # noqa: BLE001 - one failed request must not stop the batch
                events.log(f"OpenAI request failed after retries: {exc}", "warning")
                return index, None

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            futures = [pool.submit(task, i, item) for i, item in enumerate(items)]
            for future in as_completed(futures):
                index, value = future.result()
                results[index] = value
        return results
