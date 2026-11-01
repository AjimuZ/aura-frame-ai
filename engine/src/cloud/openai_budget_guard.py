"""Budget guard for OpenAI Quality Mode.

Tracks the running estimated spend and enforces hard limits BEFORE a request is made: a
request whose estimated cost would push spend past the hard stop is refused, so the run
can never exceed the configured cap. A one-time warning is emitted at the soft stop.
"""

from typing import Any

from .. import progress_events as events


class BudgetGuard:
    """Reserves estimated cost per request and stops cleanly at the hard limit."""

    # Loads the budget thresholds from config (USD).
    def __init__(self, config: dict[str, Any], override_max: float | None = None) -> None:
        budget = config.get("openai", {}).get("budget", {})
        self.max_usd = float(override_max if override_max is not None else budget.get("max_usd", 16.0))
        self.soft_stop = float(budget.get("soft_stop_usd", self.max_usd * 0.9))
        self.absolute_max = float(budget.get("absolute_max_usd", self.max_usd))
        hard = float(budget.get("hard_stop_usd", self.max_usd))
        # The effective ceiling never exceeds the absolute maximum or the (optional) override.
        self.hard_stop = min(hard, self.absolute_max, self.max_usd)
        self._spent = 0.0
        self._soft_warned = False
        self._stopped = False

    # Total estimated USD reserved so far.
    @property
    def spent_usd(self) -> float:
        return self._spent

    # Estimated USD still available before the hard stop.
    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.hard_stop - self._spent)

    # True once the hard limit has been hit and further requests are blocked.
    @property
    def stopped(self) -> bool:
        return self._stopped

    # Returns True if a request of the given estimated cost can still be afforded.
    def can_afford(self, cost: float) -> bool:
        return not self._stopped and (self._spent + cost) <= self.hard_stop

    # Reserves the estimated cost of a request. Returns False (and emits budget_stopped once)
    # when the request would exceed the hard limit; True otherwise. Emits a soft warning once.
    def reserve(self, cost: float) -> bool:
        if not self.can_afford(cost):
            if not self._stopped:
                self._stopped = True
                events.budget_stopped(self._spent)
            return False
        self._spent += cost
        if self._spent >= self.soft_stop and not self._soft_warned:
            self._soft_warned = True
            events.budget_warning(self._spent)
        return True
