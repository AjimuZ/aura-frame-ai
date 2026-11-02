"""The VisionProvider contract: result types, prompts and settings shared by every provider.

A provider judges anonymous, resized, EXIF-free JPEG copies. It never receives a path or a
file name, and it never raises out of a call: a failure comes back as a result with
`ok=False`, so one bad call cannot stop a run.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

# Bounds that no configuration can exceed. Each setting is clamped into its (min, max) range.
TIMEOUT_BOUNDS = (0.25, 600.0)
RETRY_BOUNDS = (0, 5)
CALL_LIMIT_BOUNDS = (1, 20000)
FAILURE_LIMIT_BOUNDS = (1, 20)
TOKEN_BOUNDS = (64, 2048)
RETRY_DELAY_BOUNDS = (0.0, 10.0)

# Tags a verdict may give as the reasons for a choice; the same list is in the verdict schema.
REASON_TAGS = ("composition", "light", "color", "sharpness", "moment", "subject_clarity", "other")


@dataclass(frozen=True)
class Prompt:
    """A versioned instruction. The version is part of the verdict cache key."""

    name: str
    version: str
    text: str


@dataclass(frozen=True)
class ProviderHealth:
    """What a probe found: whether the service answers and whether the model is present."""

    reachable: bool
    model_available: bool
    message: str

    # True only when a call can be expected to work.
    @property
    def ready(self) -> bool:
        return self.reachable and self.model_available


@dataclass(frozen=True)
class Comparison:
    """The verdict on one ordered pair. `winner` is "A", "B" or "tie", and None when `ok` is False.

    `error` is a short code and message without any model text, image data or path.
    """

    ok: bool
    winner: str | None = None
    confidence: str | None = None
    reasons: tuple[str, ...] = ()
    note: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    attempts: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Critique:
    """A structured explanation of one photograph. It never changes a rank."""

    ok: bool
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    caption: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    attempts: int = 0
    error: str | None = None


class VisionProvider(Protocol):
    """One small interface for every judge (local model, optional cloud sidecar)."""

    # Probes the service and the model without running inference. Never raises.
    def health(self) -> ProviderHealth: ...

    # Judges two prepared copies in the order given. Never raises; a failure has ok=False.
    def compare(self, first: bytes, second: bytes, prompt: Prompt) -> Comparison: ...

    # Critiques one prepared copy. Never raises; a failure has ok=False.
    def critique(self, image: bytes, prompt: Prompt) -> Critique: ...


# Clamps a configured number into [low, high]; a missing or non-numeric value gives the default.
def _bounded(value: Any, default: float, bounds: tuple[float, float], cast: type) -> Any:
    low, high = bounds
    try:
        if isinstance(value, bool):
            raise ValueError("a boolean is not a number")
        number = cast(value)
    except (TypeError, ValueError):
        number = cast(default)
    if number != number:  # NaN is neither below nor above a bound
        number = cast(default)
    return cast(min(max(number, low), high))


@dataclass(frozen=True)
class ProviderSettings:
    """Endpoint, model and bounded limits for one provider.

    Always build it with `from_mapping`: it clamps every limit, so no configuration can ask for
    unbounded retries, an unbounded wait or an unbounded number of calls.
    """

    endpoint: str = "http://vision:8080"
    model: str = ""
    timeout_seconds: float = 120.0
    max_retries: int = 2
    retry_delay_seconds: float = 0.5
    max_calls: int = 1000
    max_consecutive_failures: int = 3
    max_tokens: int = 512

    # Builds settings from the `ai.local` mapping of the engine configuration. Unknown keys are
    # ignored, and every numeric limit is clamped to its bounds.
    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "ProviderSettings":
        raw = values or {}
        defaults = cls()
        endpoint = str(raw.get("endpoint") or defaults.endpoint).strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("The provider endpoint must start with http:// or https://.")
        return cls(
            endpoint=endpoint,
            model=str(raw.get("model") or "").strip(),
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
            max_tokens=_bounded(raw.get("max_tokens"), defaults.max_tokens, TOKEN_BOUNDS, int),
        )


@dataclass
class ProviderStatus:
    """What the UI needs to say when a run fell back to the prescore."""

    degraded: bool = False
    reason: str | None = None
    calls: int = 0
    failures: int = 0
