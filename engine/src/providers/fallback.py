"""Keeps a run alive when a provider fails: a call budget, a failure breaker and a prescore verdict.

`GuardedProvider` wraps any VisionProvider. Once the provider has failed too often in a row, or
the call limit is reached, it stops calling it and reports `degraded`, so the caller ranks with
the local prescore and tells the user. `compare_or_prescore` turns a failed comparison into a
prescore verdict, so the tournament never stalls on one bad call.
"""

import logging

from .contract import (
    Comparison,
    Critique,
    Prompt,
    ProviderHealth,
    ProviderSettings,
    ProviderStatus,
    VisionProvider,
)

PRESCORE_SOURCE = "prescore"

_LOG = logging.getLogger(__name__)


class GuardedProvider:
    """Counts calls and failures of an inner provider and stops using it when it keeps failing."""

    # Wraps `inner`; the limits come from `settings` and are already clamped.
    def __init__(self, inner: VisionProvider, settings: ProviderSettings) -> None:
        self._inner = inner
        self._settings = settings
        self._consecutive = 0
        self.status = ProviderStatus()

    # Marks the provider as degraded once; the first reason is the one the UI shows.
    def _degrade(self, reason: str) -> None:
        if not self.status.degraded:
            self.status.degraded = True
            self.status.reason = reason
            _LOG.warning("Provider degraded to the prescore: %s", reason)

    # Adopts the degraded state of an inner provider that keeps its own status, such as the Codex
    # provider at its call cap, so a caller reading `status` of the guard sees it.
    def _follow_inner(self) -> None:
        inner = getattr(self._inner, "status", None)
        if getattr(inner, "degraded", False) is True:
            self._degrade(str(getattr(inner, "reason", None) or "The provider reported itself degraded."))

    # Reserves one call. Returns an error code when the provider must not be called, else None.
    def _admit(self) -> str | None:
        self._follow_inner()
        if self.status.degraded:
            return f"degraded: {self.status.reason}"
        if self.status.calls >= self._settings.max_calls:
            self._degrade(f"The limit of {self._settings.max_calls} calls was reached.")
            return f"call_limit: {self.status.reason}"
        self.status.calls += 1
        return None

    # Records the outcome of one call and opens the breaker after too many failures in a row.
    def _record(self, ok: bool, error: str | None) -> None:
        if ok:
            self._consecutive = 0
            return
        self.status.failures += 1
        self._consecutive += 1
        self._follow_inner()
        if self._consecutive >= self._settings.max_consecutive_failures:
            self._degrade(f"The provider failed {self._consecutive} times in a row ({error}).")

    # Probes the inner provider; a probe that raises is reported as unreachable.
    def health(self) -> ProviderHealth:
        try:
            return self._inner.health()
        except Exception:  # noqa: BLE001 - the contract says health never raises
            return ProviderHealth(False, False, "The provider health check failed unexpectedly.")

    # Compares through the inner provider unless it is degraded; never raises.
    def compare(self, first: bytes, second: bytes, prompt: Prompt) -> Comparison:
        refused = self._admit()
        if refused:
            return Comparison(ok=False, prompt_version=prompt.version, error=refused)
        try:
            result = self._inner.compare(first, second, prompt)
        except Exception:  # noqa: BLE001 - one bad call must not stop the run
            result = Comparison(
                ok=False, prompt_version=prompt.version, error="unexpected: the provider raised"
            )
        self._record(result.ok, result.error)
        return result

    # Critiques through the inner provider unless it is degraded; never raises.
    def critique(self, image: bytes, prompt: Prompt) -> Critique:
        refused = self._admit()
        if refused:
            return Critique(ok=False, prompt_version=prompt.version, error=refused)
        try:
            result = self._inner.critique(image, prompt)
        except Exception:  # noqa: BLE001 - one bad call must not stop the run
            result = Critique(
                ok=False, prompt_version=prompt.version, error="unexpected: the provider raised"
            )
        self._record(result.ok, result.error)
        return result


# Decides a pair from the local prescores: the higher wins, and a gap within `margin` is a tie.
# The result has low confidence and the source "prescore", so reports can tell it from a model verdict.
def prescore_comparison(
    first_score: float, second_score: float, prompt: Prompt, margin: float = 1.0
) -> Comparison:
    gap = first_score - second_score
    winner = "tie" if abs(gap) <= margin else ("A" if gap > 0 else "B")
    return Comparison(
        ok=True,
        winner=winner,
        confidence="low",
        reasons=("other",),
        note="Decided by the local prescore.",
        provider=PRESCORE_SOURCE,
        prompt_version=prompt.version,
    )


# Compares two copies through the provider and falls back to the prescores when the call fails.
# Always returns a usable verdict, so a failed provider degrades the run instead of stopping it.
def compare_or_prescore(
    provider: VisionProvider,
    first: bytes,
    second: bytes,
    prompt: Prompt,
    prescores: tuple[float, float],
) -> Comparison:
    result = provider.compare(first, second, prompt)
    if result.ok:
        return result
    return prescore_comparison(prescores[0], prescores[1], prompt)
