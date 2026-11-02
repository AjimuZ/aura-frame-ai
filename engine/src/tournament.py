"""Swiss tournament over the shortlist: both orders per pair, a verdict cache and a Bradley-Terry rank.

Each round pairs photos with similar running scores that have not met. Every pair is judged twice, once in each
order, and the pair counts as a win only when both orders pick the same photo; any disagreement (position bias, or a
tie in either order) is a tie. Verdicts are cached by content hash, provider, model and prompt version, so a rerun
repeats no call. The rank comes from the pairwise outcomes alone, with the prescore as a weak prior. Without a
usable provider the stage is skipped and the prescore decides. Images are fetched only when a call is really made.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import db
from . import progress_events as events
from .bradley_terry import Outcome, Rating, fit_bradley_terry
from .providers import COMPARE_PROMPT, Prompt, VisionProvider
from .providers.contract import _bounded
from .shortlist import Candidate, tag_limit

WINNERS = ("A", "B", "tie")
# Backtracking steps allowed per pairing attempt; far above what a shortlist of 64 needs, a guard against a blow-up.
PAIRING_STEP_LIMIT = 20000


@dataclass(frozen=True)
class TournamentSettings:
    """Shortlist and tournament limits. Build it with `from_mapping`, which clamps every value."""

    shortlist_size: int = 32
    max_tag_share: float = 0.25
    prior_scale: float = 0.5
    prior_precision: float = 0.5
    max_failed_in_a_row: int = 3

    # Builds settings from the `tournament` mapping of the engine configuration; unknown keys are ignored.
    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "TournamentSettings":
        raw = values or {}
        d = cls()
        return cls(
            shortlist_size=_bounded(raw.get("shortlist_size"), d.shortlist_size, (2, 500), int),
            max_tag_share=_bounded(raw.get("max_tag_share"), d.max_tag_share, (0.05, 1.0), float),
            prior_scale=_bounded(raw.get("prior_scale"), d.prior_scale, (0.0, 2.0), float),
            prior_precision=_bounded(raw.get("prior_precision"), d.prior_precision, (0.05, 10.0), float),
            max_failed_in_a_row=_bounded(raw.get("max_failed_in_a_row"), d.max_failed_in_a_row, (1, 50), int),
        )

    # The most photos of one content tag the shortlist may hold.
    @property
    def max_per_tag(self) -> int:
        return tag_limit(self.shortlist_size, self.max_tag_share)


@dataclass(frozen=True)
class PairResult:
    """The outcome of one pair after both orders. `winner_id` is None for a tie.

    `orders_agree` is False when the two orders picked different photos (or one was a tie).
    """

    first_id: str
    second_id: str
    winner_id: str | None
    orders_agree: bool


@dataclass
class TournamentResult:
    """What the stage produced: the ranking (best first) and how it was reached.

    `skipped` is True when no pairwise judgment shaped the ranking and the prescore decided; `reason` then says why
    ("no_provider", "provider_not_ready", "too_few_photos" or "no_verdicts"). `rounds_played` can be below
    `rounds_planned` when no unmet pairing remains or the provider keeps failing. `failed_pairs` are pairs left
    without an outcome; they are not cached, so a rerun tries them again.
    """

    ratings: list[Rating]
    skipped: bool = False
    reason: str | None = None
    rounds_planned: int = 0
    rounds_played: int = 0
    pairs: list[PairResult] = field(default_factory=list)
    failed_pairs: int = 0
    provider_calls: int = 0
    cache_hits: int = 0
    degraded: bool = False


# Returns ceil(log2(size)) + 1 Swiss rounds, and 0 when there is nothing to pair.
def rounds_for(size: int) -> int:
    return 0 if size < 2 else (size - 1).bit_length() + 1


# Finds pairs for the whole `order` (best first) by backtracking, preferring neighbours, never repeating a met pair.
# Returns None when no complete pairing exists or the step budget runs out.
def _perfect_pairing(order: list[str], met: set[frozenset], budget: list[int]) -> list[tuple[str, str]] | None:
    if not order:
        return []
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    first, rest = order[0], order[1:]
    for position, partner in enumerate(rest):
        if frozenset((first, partner)) in met:
            continue
        tail = _perfect_pairing(rest[:position] + rest[position + 1 :], met, budget)
        if tail is not None:
            return [(first, partner), *tail]
    return None


# Pairs each photo with the nearest one it has not met; a photo without such a partner is left out.
def _greedy_pairing(order: list[str], met: set[frozenset]) -> list[tuple[str, str]]:
    free, pairs = list(order), []
    while free:
        first = free.pop(0)
        for position, partner in enumerate(free):
            if frozenset((first, partner)) not in met:
                pairs.append((first, free.pop(position)))
                break
    return pairs


# Pairs one round from photos sorted best first. With an odd count the lowest photo that has no bye yet sits out,
# unless that makes a complete pairing impossible. Returns (pairs, bye); pairs is empty when nobody can meet anew.
def _pair_round(order: list[str], met: set[frozenset], byes: set[str]) -> tuple[list[tuple[str, str]], str | None]:
    if len(order) % 2 == 0:
        pairs = _perfect_pairing(order, met, [PAIRING_STEP_LIMIT])
        return (pairs if pairs is not None else _greedy_pairing(order, met)), None
    lowest_first = list(reversed(order))
    candidates = [p for p in lowest_first if p not in byes] + [p for p in lowest_first if p in byes]
    for bye in candidates:
        pairs = _perfect_pairing([p for p in order if p != bye], met, [PAIRING_STEP_LIMIT])
        if pairs is not None:
            return pairs, bye
    bye = candidates[0]
    return _greedy_pairing([p for p in order if p != bye], met), bye


# Reads cached verdicts and asks the provider for the rest; every real call is cached under its full key.
class _Judge:
    # Binds the provider, the cache and the copy loader; `provider_name`, `model` and the prompt version key the cache.
    def __init__(
        self,
        provider: VisionProvider,
        conn: Any,
        copy_for: Callable[[str], bytes],
        provider_name: str,
        model: str,
        prompt: Prompt,
    ) -> None:
        self._provider = provider
        self._conn = conn
        self._copy_for = copy_for
        self._identity = (provider_name, model, prompt.version)
        self._prompt = prompt
        self.calls = 0
        self.cache_hits = 0

    # Returns "A", "B" or "tie" for the ordered pair, or None when no verdict could be had. A cache hit makes no
    # call and loads no image. A failed call, an unreadable copy or a malformed answer is not cached.
    def verdict(self, first_id: str, second_id: str) -> str | None:
        key = (first_id, second_id, *self._identity)
        cached = db.load_verdict(self._conn, key)
        if cached is not None:
            self.cache_hits += 1
            return cached["winner"]
        try:
            first, second = self._copy_for(first_id), self._copy_for(second_id)
        except (OSError, ValueError):
            return None
        self.calls += 1
        try:
            result = self._provider.compare(first, second, self._prompt)
        except Exception:  # noqa: BLE001 - the contract says compare never raises; one bad call must not stop a run
            return None
        if not result.ok or result.winner not in WINNERS:
            return None
        db.save_verdict(self._conn, key, result.winner, result.confidence, result.reasons, result.note)
        return result.winner

    # Judges a pair in both orders. Returns None if either order has no verdict; the second order is not asked
    # when the first failed, so a dead provider costs one call per pair.
    def pair(self, a_id: str, b_id: str) -> PairResult | None:
        forward = self.verdict(a_id, b_id)
        if forward is None:
            return None
        backward = self.verdict(b_id, a_id)
        if backward is None:
            return None
        picked_forward = {"A": a_id, "B": b_id, "tie": None}[forward]
        picked_backward = {"A": b_id, "B": a_id, "tie": None}[backward]
        agree = picked_forward == picked_backward
        return PairResult(a_id, b_id, picked_forward if agree else None, agree)


# Ranks by prescore alone, best first, for a run in which no pairwise judgment took part.
def _prescore_result(members: Mapping[str, Candidate], reason: str, rounds_planned: int = 0, **counts: Any) -> TournamentResult:
    order = sorted(members.values(), key=lambda c: (-c.prescore, c.photo_id))
    ratings = [Rating(c.photo_id, rank, c.prescore, None, "prescore") for rank, c in enumerate(order, start=1)]
    return TournamentResult(ratings, True, reason, rounds_planned=rounds_planned, **counts)


# True once a guarded provider has stopped calling its inner provider (too many failures or the call limit).
def _is_degraded(provider: VisionProvider) -> bool:
    return bool(getattr(getattr(provider, "status", None), "degraded", False))


# Runs the Swiss tournament over `shortlist` and ranks it. `copy_for(photo_id)` returns the prepared anonymous copy
# and is called only for a verdict that is not cached. `provider_name` and `model` are the cache identity of the
# judge. With no provider, a provider that is not ready, or fewer than two photos, the result is `skipped` and ordered
# by prescore. A failing provider ends the rounds early (guard degraded, or `max_failed_in_a_row` pairs in a row), and
# the ranking uses the outcomes gathered so far. Never raises for a provider failure.
def run_tournament(
    shortlist: Sequence[Candidate],
    provider: VisionProvider | None,
    conn: Any,
    copy_for: Callable[[str], bytes],
    provider_name: str,
    model: str,
    prompt: Prompt = COMPARE_PROMPT,
    settings: TournamentSettings | None = None,
) -> TournamentResult:
    settings = settings or TournamentSettings()
    members = {candidate.photo_id: candidate for candidate in shortlist}
    rounds = rounds_for(len(members))
    if provider is None:
        events.log("No judge is configured; the prescore decides the order.")
        return _prescore_result(members, "no_provider", rounds)
    if len(members) < 2:
        return _prescore_result(members, "too_few_photos", rounds)
    try:
        ready = provider.health().ready
    except Exception:  # noqa: BLE001 - a probe that raises counts as not ready
        ready = False
    if not ready:
        events.log("The judge is not ready; the prescore decides the order.", "warning")
        return _prescore_result(members, "provider_not_ready", rounds)

    judge = _Judge(provider, conn, copy_for, provider_name, model, prompt)
    prescores = {photo_id: candidate.prescore for photo_id, candidate in members.items()}
    scores = {photo_id: 0.0 for photo_id in members}
    met: set[frozenset] = set()
    byes: set[str] = set()
    results: list[PairResult] = []
    failed = failed_in_a_row = played = 0
    stop = False
    for round_number in range(1, rounds + 1):
        if stop:
            break
        order = sorted(members, key=lambda p: (-scores[p], -prescores[p], p))
        matches, bye = _pair_round(order, met, byes)
        if not matches:
            break
        if bye is not None:
            byes.add(bye)
        played += 1
        for a_id, b_id in matches:
            met.add(frozenset((a_id, b_id)))
            result = judge.pair(a_id, b_id)
            if result is None:
                failed += 1
                failed_in_a_row += 1
                if failed_in_a_row >= settings.max_failed_in_a_row or _is_degraded(provider):
                    stop = True
                    break
                continue
            failed_in_a_row = 0
            results.append(result)
            share = 0.5 if result.winner_id is None else (1.0 if result.winner_id == a_id else 0.0)
            scores[a_id] += share
            scores[b_id] += 1.0 - share
        events.progress("tournament", round_number, rounds, f"tournament round {round_number}/{rounds}")

    degraded = _is_degraded(provider)
    counts = dict(
        rounds_played=played, pairs=results, failed_pairs=failed,
        provider_calls=judge.calls, cache_hits=judge.cache_hits, degraded=degraded,
    )  # fmt: skip
    if not results:
        events.log("The judge gave no usable verdict; the prescore decides the order.", "warning")
        return _prescore_result(members, "no_verdicts", rounds, **counts)
    outcomes = [
        Outcome(r.first_id, r.second_id, 0.5 if r.winner_id is None else (1.0 if r.winner_id == r.first_id else 0.0))
        for r in results
    ]
    ratings = fit_bradley_terry(prescores, outcomes, settings.prior_scale, settings.prior_precision)
    return TournamentResult(ratings, False, None, rounds_planned=rounds, **counts)
