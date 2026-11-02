"""VLM runner: probes the local vision provider before the optional model stage of a run.

The runner talks to the local llama-server through the VisionProvider contract
(engine/src/providers). Judging happens on shortlisted pairs, never as absolute per-photo
votes, so until the pairwise tournament is wired in (package P04) this stage only checks the
provider and leaves the technical, face and aesthetic ranking untouched.
"""

from typing import Any

from .. import progress_events as events
from ..providers import LlamaServerProvider, ProviderHealth, ProviderSettings
from ..utils import Photo


class VlmRunner:
    """Checks that the configured local vision provider can be used."""

    # Builds the provider from the `ai.local` mapping of the configuration. An invalid endpoint
    # is kept as a message instead of raising, so a bad setting never stops a run.
    def __init__(self, config: dict[str, Any]) -> None:
        self._provider: LlamaServerProvider | None = None
        self._setup_error = ""
        try:
            settings = ProviderSettings.from_mapping((config.get("ai") or {}).get("local"))
        except ValueError as exc:
            self._setup_error = str(exc)
            return
        self._provider = LlamaServerProvider(settings)

    # Probes the service and the model without running inference. Never raises.
    def health(self) -> ProviderHealth:
        if self._provider is None:
            return ProviderHealth(False, False, self._setup_error)
        return self._provider.health()

    # Releases the provider's connection pool.
    def close(self) -> None:
        if self._provider is not None:
            self._provider.close()


# Probes the local vision provider for the candidate stage and says what to do when it is not
# ready. Candidates are not changed: the run keeps the local ranking.
def judge_candidates(candidates: list[Photo], config: dict[str, Any]) -> None:
    runner = VlmRunner(config)
    try:
        health = runner.health()
    finally:
        runner.close()
    if not health.ready:
        events.log(health.message, "error")
        events.log("Skipping the model stage; using the local ranking only.", "warning")
        return
    events.log(
        f"The local vision provider is ready, but pairwise judging is not wired in yet; "
        f"keeping the local ranking for {len(candidates)} candidates.",
        "warning",
    )
