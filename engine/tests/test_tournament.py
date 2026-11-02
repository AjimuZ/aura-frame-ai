"""Shortlist, Swiss tournament in both orders, verdict cache and Bradley-Terry rank, with a fake provider only.

No image, model or network is used: a copy is just the photo id as bytes, and the fake judge decides from a hidden
quality per photo.
"""

import math
import random

import pytest

from src import db
from src.bradley_terry import Outcome, fit_bradley_terry
from src.providers import COMPARE_PROMPT, Comparison, GuardedProvider, Prompt, ProviderHealth, ProviderSettings
from src.shortlist import Candidate, select_shortlist, tag_limit
from src.tournament import TournamentSettings, rounds_for, run_tournament


class FakeJudge:
    """Prefers the photo with the higher hidden quality; `biased` always answers "A", `fail` always errors."""

    def __init__(self, quality: dict[str, float] | None = None, biased: bool = False, fail: bool = False, ready: bool = True) -> None:
        self.quality = quality or {}
        self.biased, self.fail, self.ready = biased, fail, ready
        self.calls: list[tuple[str, str]] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(True, self.ready, "fake")

    def compare(self, first: bytes, second: bytes, prompt: Prompt) -> Comparison:
        a, b = first.decode(), second.decode()
        self.calls.append((a, b))
        if self.fail:
            return Comparison(ok=False, prompt_version=prompt.version, error="down: fake outage")
        if self.biased:
            winner = "A"
        else:
            winner = "A" if self.quality.get(a, 0.0) >= self.quality.get(b, 0.0) else "B"
        return Comparison(
            ok=True, winner=winner, confidence="high", reasons=("light",), note="fake",
            provider="fake", model="m1", prompt_version=prompt.version, attempts=1,
        )  # fmt: skip

    def critique(self, image: bytes, prompt: Prompt):  # pragma: no cover - the tournament never critiques
        raise AssertionError("the tournament must not critique")


def ids(count: int) -> list[str]:
    return [f"p{i:02d}" for i in range(count)]


# Candidates whose prescore falls with the index, so the prescore order is p00, p01, ...
def candidates(count: int) -> list[Candidate]:
    return [Candidate(photo_id, float(count - i)) for i, photo_id in enumerate(ids(count))]


def copy_for(photo_id: str) -> bytes:
    return photo_id.encode()


@pytest.fixture()
def conn(data_config: dict):
    return db.connect(data_config)


def run(shortlist, judge, conn, model="m1", prompt=COMPARE_PROMPT, provider_name="fake", loader=copy_for, settings=None):
    return run_tournament(shortlist, judge, conn, loader, provider_name, model, prompt, settings)


# --- shortlist ------------------------------------------------------------------------------------------------------


def test_the_shortlist_takes_the_top_group_representatives() -> None:
    pool = [
        Candidate("a1", 0.9, group_id=1), Candidate("a2", 0.5, group_id=1),
        Candidate("b1", 0.8, group_id=2), Candidate("c1", 0.7), Candidate("c2", 0.6, group_id=-1),
        Candidate("d1", 0.4, group_id=3),
    ]  # fmt: skip
    chosen = select_shortlist(pool, size=3, max_per_tag=5)
    assert [c.photo_id for c in chosen.members] == ["a1", "b1", "c1"]
    assert chosen.alternates == {"a1": ("a2",), "b1": (), "c1": ()}
    everything = select_shortlist(pool, size=10, max_per_tag=5)
    assert [c.photo_id for c in everything.members] == ["a1", "b1", "c1", "c2", "d1"]  # one per group, ungrouped alone


def test_the_shortlist_limits_each_content_tag() -> None:
    pool = [Candidate(f"land{i}", 1.0 - i / 100, tag="landscape") for i in range(6)]
    pool += [Candidate("pet0", 0.1, tag="pet"), Candidate("free", 0.05, tag=None), Candidate("free2", 0.04, tag=None)]
    chosen = select_shortlist(pool, size=6, max_per_tag=2)
    assert [c.photo_id for c in chosen.members] == ["land0", "land1", "pet0", "free", "free2"]
    assert chosen.skipped_by_tag == 4  # the cap is not relaxed to fill the list


def test_the_shortlist_does_not_depend_on_input_order() -> None:
    pool = [Candidate(f"x{i}", float(i % 4), group_id=i % 5, tag="a" if i % 2 else "b") for i in range(20)]
    expected = select_shortlist(pool, 4, 2)
    for seed in range(5):
        shuffled = pool[:]
        random.Random(seed).shuffle(shuffled)
        assert select_shortlist(shuffled, 4, 2) == expected


def test_the_shortlist_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        select_shortlist([], 0, 1)
    with pytest.raises(ValueError):
        select_shortlist([], 1, 0)
    with pytest.raises(ValueError):
        Candidate("x", float("nan"))
    assert tag_limit(32, 0.25) == 8 and tag_limit(1, 0.05) == 1


# --- rounds, both orders, ties --------------------------------------------------------------------------------------


@pytest.mark.parametrize("size, rounds", [(0, 0), (1, 0), (2, 2), (3, 3), (4, 3), (8, 4), (9, 5), (32, 6), (33, 7), (64, 7)])
def test_swiss_rounds_are_ceil_log2_plus_one(size: int, rounds: int) -> None:
    assert rounds_for(size) == rounds
    if size >= 2:
        assert rounds == math.ceil(math.log2(size)) + 1


@pytest.mark.parametrize("size", [8, 32])
def test_every_pair_runs_in_both_orders_for_the_planned_rounds(size: int, conn) -> None:
    quality = {photo_id: random.Random(7).random() + i for i, photo_id in enumerate(ids(size))}
    judge = FakeJudge(quality)
    result = run(candidates(size), judge, conn)
    assert result.rounds_planned == result.rounds_played == math.ceil(math.log2(size)) + 1
    assert len(result.pairs) == size // 2 * result.rounds_played
    assert len({frozenset((p.first_id, p.second_id)) for p in result.pairs}) == len(result.pairs)  # no rematch
    assert len(judge.calls) == 2 * len(result.pairs) == result.provider_calls
    for pair in result.pairs:
        assert (pair.first_id, pair.second_id) in judge.calls and (pair.second_id, pair.first_id) in judge.calls
    assert not result.skipped and result.failed_pairs == 0
    assert [r.rank for r in result.ratings] == list(range(1, size + 1))


def test_an_odd_shortlist_gives_each_round_a_bye(conn) -> None:
    result = run(candidates(3), FakeJudge({"p00": 1, "p01": 2, "p02": 3}), conn)
    assert result.rounds_played == 3 and len(result.pairs) == 3
    assert {r.photo_id for r in result.ratings} == set(ids(3))


def test_two_photos_meet_once_and_the_second_round_finds_no_new_pair(conn) -> None:
    result = run(candidates(2), FakeJudge({"p00": 1, "p01": 2}), conn)
    assert (result.rounds_planned, result.rounds_played, len(result.pairs)) == (2, 1, 1)
    assert [r.photo_id for r in result.ratings] == ["p01", "p00"]


def test_disagreement_between_the_orders_counts_as_a_tie(conn) -> None:
    judge = FakeJudge(biased=True)  # always "A": the first photo shown wins in both orders, so the orders disagree
    result = run(candidates(8), judge, conn)
    assert result.pairs and all(not p.orders_agree and p.winner_id is None for p in result.pairs)
    assert [r.photo_id for r in result.ratings] == ids(8)  # only ties: the prescore prior decides


def test_agreeing_orders_give_a_win_and_the_best_photo_ranks_first(conn) -> None:
    quality = {photo_id: float(i) for i, photo_id in enumerate(ids(8))}  # the reverse of the prescore order
    result = run(candidates(8), FakeJudge(quality), conn)
    assert all(p.orders_agree and p.winner_id is not None for p in result.pairs)
    assert result.ratings[0].photo_id == "p07"
    assert result.ratings[0].uncertainty is not None and result.ratings[0].source == "pairwise"


# --- verdict cache --------------------------------------------------------------------------------------------------


def test_a_rerun_makes_no_provider_call_and_loads_no_image(conn) -> None:
    quality = {photo_id: float(i) for i, photo_id in enumerate(ids(8))}
    first = run(candidates(8), FakeJudge(quality), conn)
    loaded: list[str] = []

    def loader(photo_id: str) -> bytes:
        loaded.append(photo_id)
        return copy_for(photo_id)

    second_judge = FakeJudge(quality)
    second = run(candidates(8), second_judge, conn, loader=loader)
    assert second_judge.calls == [] and loaded == []
    assert second.provider_calls == 0 and second.cache_hits == first.provider_calls
    assert [r.photo_id for r in second.ratings] == [r.photo_id for r in first.ratings]


@pytest.mark.parametrize(
    "change",
    [{"model": "m2"}, {"prompt": Prompt("compare", "compare-2", "text")}, {"provider_name": "other"}],
    ids=["model", "prompt_version", "provider"],
)
def test_a_changed_model_prompt_version_or_provider_misses_the_cache(change: dict, conn) -> None:
    quality = {photo_id: float(i) for i, photo_id in enumerate(ids(4))}
    first = run(candidates(4), FakeJudge(quality), conn)
    judge = FakeJudge(quality)
    again = run(candidates(4), judge, conn, **change)
    assert again.cache_hits == 0 and len(judge.calls) == first.provider_calls > 0


def test_the_cache_knows_the_order_of_the_pair(conn) -> None:
    key = ("p00", "p01", "fake", "m1", "compare-1")
    db.save_verdict(conn, key, "A", "high", ("light",), "ok")
    assert db.load_verdict(conn, key) == {"winner": "A", "confidence": "high", "reasons": ("light",), "note": "ok"}
    assert db.load_verdict(conn, ("p01", "p00", "fake", "m1", "compare-1")) is None
    with pytest.raises(ValueError):
        db.save_verdict(conn, key, "maybe", None, (), "")


def test_failed_calls_are_not_cached_so_a_rerun_completes_them(conn) -> None:
    broken = run(candidates(4), FakeJudge(fail=True), conn)
    assert broken.skipped and broken.reason == "no_verdicts"
    assert conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0] == 0
    healthy = FakeJudge({photo_id: float(i) for i, photo_id in enumerate(ids(4))})
    fixed = run(candidates(4), healthy, conn)
    assert not fixed.skipped and fixed.provider_calls == len(healthy.calls) > 0


# --- no provider, failures ------------------------------------------------------------------------------------------


def test_with_no_provider_the_stage_is_skipped_and_the_prescore_decides(conn) -> None:
    shortlist = [Candidate("low", 0.2), Candidate("high", 0.9), Candidate("mid", 0.5)]
    result = run(shortlist, None, conn)
    assert result.skipped and result.reason == "no_provider" and result.provider_calls == 0
    assert [(r.photo_id, r.rank, r.source, r.uncertainty) for r in result.ratings] == [
        ("high", 1, "prescore", None), ("mid", 2, "prescore", None), ("low", 3, "prescore", None),
    ]  # fmt: skip


def test_a_provider_that_is_not_ready_is_not_called(conn) -> None:
    judge = FakeJudge(ready=False)
    result = run(candidates(4), judge, conn)
    assert result.skipped and result.reason == "provider_not_ready" and judge.calls == []
    assert run(candidates(1), FakeJudge(), conn).reason == "too_few_photos"


def test_a_dead_provider_stops_after_a_few_failed_pairs(conn) -> None:
    judge = FakeJudge(fail=True)
    result = run(candidates(8), judge, conn, settings=TournamentSettings(max_failed_in_a_row=3))
    assert result.skipped and result.reason == "no_verdicts"
    assert result.failed_pairs == 3 and len(judge.calls) == 3
    assert [r.photo_id for r in result.ratings] == ids(8)


def test_a_degraded_guard_ends_the_tournament_and_is_reported(conn) -> None:
    guard = GuardedProvider(FakeJudge(fail=True), ProviderSettings.from_mapping({"max_consecutive_failures": 1}))
    result = run(candidates(8), guard, conn)
    assert result.degraded and result.skipped and result.failed_pairs == 1


def test_an_unreadable_copy_fails_only_its_pair(conn) -> None:
    def loader(photo_id: str) -> bytes:
        if photo_id == "p00":
            raise OSError("unreadable")
        return copy_for(photo_id)

    judge = FakeJudge({photo_id: float(i) for i, photo_id in enumerate(ids(8))})
    result = run(candidates(8), judge, conn, loader=loader)
    assert result.failed_pairs >= 1 and result.pairs and not result.skipped
    assert all("p00" not in call for call in judge.calls)


# --- Bradley-Terry --------------------------------------------------------------------------------------------------


def test_with_no_outcomes_the_prescore_order_stays_and_uncertainty_is_the_prior() -> None:
    ratings = fit_bradley_terry({"a": 3.0, "b": 2.0, "c": 1.0}, [], prior_precision=0.5)
    assert [r.photo_id for r in ratings] == ["a", "b", "c"]
    assert all(r.uncertainty == pytest.approx(1 / math.sqrt(0.5)) for r in ratings)


def test_outcomes_give_a_rank_and_a_shrinking_uncertainty() -> None:
    prescores = {"a": 1.0, "b": 1.0, "c": 1.0}
    few = fit_bradley_terry(prescores, [Outcome("a", "b", 1.0), Outcome("b", "c", 1.0)])
    many = fit_bradley_terry(prescores, [Outcome("a", "b", 1.0), Outcome("b", "c", 1.0)] * 6)
    assert [r.photo_id for r in few] == ["a", "b", "c"] == [r.photo_id for r in many]
    assert [r.rank for r in many] == [1, 2, 3]
    by_id = {r.photo_id: r for r in many}
    assert by_id["a"].strength > by_id["b"].strength > by_id["c"].strength
    assert by_id["b"].uncertainty < {r.photo_id: r for r in few}["b"].uncertainty


def test_unbeaten_and_winless_photos_stay_finite() -> None:
    ratings = fit_bradley_terry({"a": 0.0, "b": 0.0}, [Outcome("a", "b", 1.0)] * 50)
    assert all(math.isfinite(r.strength) and math.isfinite(r.uncertainty) for r in ratings)
    assert ratings[0].photo_id == "a" and ratings[0].strength - ratings[1].strength < 10


def test_the_prescore_is_a_weak_prior_that_one_decisive_outcome_overrides() -> None:
    best_on_paper = {"high": 10.0, "low": 0.0}
    assert [r.photo_id for r in fit_bradley_terry(best_on_paper, [])] == ["high", "low"]
    overridden = fit_bradley_terry(best_on_paper, [Outcome("low", "high", 1.0)])
    assert [r.photo_id for r in overridden] == ["low", "high"]


def test_a_tie_leaves_the_prescore_to_break_it_and_the_scale_does_not_matter() -> None:
    tied = [Outcome("a", "b", 0.5)]
    small = fit_bradley_terry({"a": 0.3, "b": 0.6}, tied)
    large = fit_bradley_terry({"a": 30.0, "b": 60.0}, tied)
    assert [r.photo_id for r in small] == ["b", "a"] == [r.photo_id for r in large]
    assert [r.strength for r in small] == pytest.approx([r.strength for r in large])


def test_bradley_terry_is_deterministic_and_validates_outcomes() -> None:
    prescores = {"a": 1.0, "b": 2.0, "c": 3.0}
    outcomes = [Outcome("a", "b", 1.0), Outcome("c", "a", 0.5)]
    assert fit_bradley_terry(prescores, outcomes) == fit_bradley_terry(prescores, list(reversed(outcomes)))
    for bad in (Outcome("a", "zzz", 1.0), Outcome("a", "a", 1.0), Outcome("a", "b", 1.5), Outcome("a", "b", float("nan"))):
        with pytest.raises(ValueError):
            fit_bradley_terry(prescores, [bad])
    with pytest.raises(ValueError):
        fit_bradley_terry(prescores, [], prior_precision=0.0)
    assert fit_bradley_terry({}, []) == []


# --- settings -------------------------------------------------------------------------------------------------------


def test_settings_are_clamped_and_the_shipped_config_matches_the_defaults(config: dict) -> None:
    assert TournamentSettings.from_mapping(config["tournament"]) == TournamentSettings()
    wild = TournamentSettings.from_mapping(
        {"shortlist_size": 10**9, "max_tag_share": -3, "prior_precision": 0, "max_failed_in_a_row": "x"}
    )
    assert (wild.shortlist_size, wild.max_tag_share, wild.prior_precision, wild.max_failed_in_a_row) == (500, 0.05, 0.05, 3)
    assert TournamentSettings.from_mapping(None) == TournamentSettings() and TournamentSettings().max_per_tag == 8
