"""Critique, diversity-aware selection, tiers, reports and exports, with a fake provider and synthetic data only.

No model, network or personal photo is used: a prepared copy is just the photo ID as bytes, the fake judge decides from
a hidden quality per photo, and exported "originals" are small files written under tmp_path.
"""

import csv
import hashlib
import random
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import db
from src.critique import MAX_CAPTION_CHARS, MAX_ITEM_CHARS, MAX_ITEMS, critique_finalists
from src.curation import CurationInput, load_stored_library, prescore, run_curation, working_copy_loader
from src.curation_export import export_copies, plan_export, safe_original_name
from src.curation_report import CSV_COLUMNS, CSV_NAME, HTML_NAME, build_rows, csv_safe, write_reports
from src.providers import Comparison, Critique, Prompt, ProviderHealth
from src.providers.images import check_copy
from src.selection import TIER_SIZES, SelectionSettings, cut_tiers, mmr_sequence, select_diverse
from src.tournament import TournamentSettings
from src.utils import Photo, working_copy_path


class FakeJudge:
    """Prefers the photo with the higher hidden quality and writes a different critique on every call."""

    def __init__(self, quality: dict[str, float] | None = None, texts: dict | None = None, ready: bool = True) -> None:
        self.quality = quality or {}
        self.texts = texts
        self.ready = ready
        self.compare_calls: list[tuple[str, str]] = []
        self.critique_calls: list[str] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(True, self.ready, "fake")

    def compare(self, first: bytes, second: bytes, prompt: Prompt) -> Comparison:
        a, b = first.decode(), second.decode()
        self.compare_calls.append((a, b))
        winner = "A" if self.quality.get(a, 0.0) >= self.quality.get(b, 0.0) else "B"
        return Comparison(
            ok=True, winner=winner, confidence="high", reasons=("light",), note="fake",
            provider="fake", model="m1", prompt_version=prompt.version, attempts=1,
        )  # fmt: skip

    def critique(self, image: bytes, prompt: Prompt) -> Critique:
        self.critique_calls.append(image.decode())
        number = len(self.critique_calls)
        texts = self.texts or {
            "strengths": (f"clean light {number}",), "weaknesses": (f"soft corner {number}",), "caption": f"caption {number}",
        }  # fmt: skip
        return Critique(
            ok=True, strengths=texts["strengths"], weaknesses=texts["weaknesses"], caption=texts["caption"],
            provider="fake", model="m1", prompt_version=prompt.version, attempts=1,
        )  # fmt: skip


class ScriptedCritic(FakeJudge):
    """Answers critiques from a list of ready-made results, in order (the last one repeats)."""

    def __init__(self, answers: list) -> None:
        super().__init__()
        self.answers = answers

    def critique(self, image: bytes, prompt: Prompt):
        self.critique_calls.append(image.decode())
        answer = self.answers[min(len(self.critique_calls) - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def ids(count: int) -> list[str]:
    return [f"p{i:03d}" for i in range(count)]


def copy_for(photo_id: str) -> bytes:
    return photo_id.encode()


# Unit vectors that differ from photo to photo, so no two photos look alike.
def random_vectors(photo_ids: list[str], seed: int = 7, dim: int = 8) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    vectors = {photo_id: rng.normal(size=dim) for photo_id in photo_ids}
    return {photo_id: (v / np.linalg.norm(v)).astype(np.float32) for photo_id, v in vectors.items()}


# Photos whose prescore falls with the index, each with its own embedding.
def library(count: int) -> list[CurationInput]:
    vectors = random_vectors(ids(count))
    return [CurationInput(photo_id, float(count - i), None, None, vectors[photo_id]) for i, photo_id in enumerate(ids(count))]


# A hidden quality that disagrees with the prescore order, so the tournament really reorders the shortlist.
def shuffled_quality(count: int) -> dict[str, float]:
    order = ids(count)
    random.Random(3).shuffle(order)
    return {photo_id: float(i) for i, photo_id in enumerate(order)}


SMALL = TournamentSettings(shortlist_size=16)


@pytest.fixture()
def conn(data_config: dict):
    return db.connect(data_config)


def curate(photos, judge, conn, **kwargs):
    selection = kwargs.pop("selection", SelectionSettings(final_size=5))
    return run_curation(photos, judge, conn, copy_for, "fake", "m1", SMALL, selection, **kwargs)


# What identifies a result: the full ranking and the selection, nothing about critiques.
def shape(result) -> tuple:
    ranking = [(e.photo_id, e.rank, e.source, e.strength, e.uncertainty, e.alternates) for e in result.ranking]
    return ranking, result.selection


# --- critique does not change the rank ----------------------------------------------------------------------------------


def test_critique_runs_for_finalists_only_and_does_not_change_the_rank(conn) -> None:
    photos, quality = library(40), shuffled_quality(40)
    plain = curate(photos, FakeJudge(quality), conn, with_critique=False)
    judge = FakeJudge(quality)
    critiqued = curate(photos, judge, conn)

    assert plain.critique.skipped and plain.critique.notes == {}
    assert len(critiqued.selection.final) == 5
    assert sorted(judge.critique_calls) == sorted(critiqued.selection.final)  # the finalists, and no one else
    assert set(critiqued.critique.notes) == set(critiqued.selection.final)
    assert shape(plain) == shape(critiqued)
    assert [e.photo_id for e in plain.ranking[:16]] != ids(40)[:16]  # the tournament did reorder the shortlist


def test_a_critique_with_different_text_leaves_the_rank_identical(conn) -> None:
    photos, quality = library(40), shuffled_quality(40)
    first = curate(photos, FakeJudge(quality), conn)
    other_text = {"strengths": ("x",) * 5, "weaknesses": ("y",) * 5, "caption": "z" * 500}
    second = curate(photos, FakeJudge(quality, texts=other_text), conn)
    nothing = curate(photos, FakeJudge(quality, texts={"strengths": (), "weaknesses": (), "caption": ""}), conn)
    assert shape(first) == shape(second) == shape(nothing)
    assert nothing.critique.notes == {} and nothing.critique.failed == 5  # an empty answer is no note


def test_the_critique_stage_takes_ids_and_returns_text_only() -> None:
    judge = FakeJudge()
    result = critique_finalists(["a", "b", "a"], judge, copy_for)
    assert judge.critique_calls == ["a", "b"]  # a repeated finalist is critiqued once
    assert set(result.notes) == {"a", "b"} and result.failed == 0 and result.provider_calls == 2
    assert {field for field in vars(result.notes["a"])} == {
        "strengths", "weaknesses", "caption", "provider", "model", "prompt_version",
    }  # fmt: skip


def test_critique_is_skipped_without_a_ready_provider_or_finalists() -> None:
    assert critique_finalists(["a"], None, copy_for).reason == "no_provider"
    unready = FakeJudge(ready=False)
    assert critique_finalists(["a"], unready, copy_for).reason == "provider_not_ready" and unready.critique_calls == []
    assert critique_finalists([], FakeJudge(), copy_for).reason == "no_finalists"


def test_critique_text_is_bounded_and_cleaned() -> None:
    long = Critique(
        ok=True, strengths=("a\x00b\nc " + "x" * 500,) + ("s",) * 9 + ("",), weaknesses=("  ", 7), caption="c" * 900,  # type: ignore[arg-type]
        provider="fake", model="m", prompt_version="critique-1",
    )  # fmt: skip
    note = critique_finalists(["a"], ScriptedCritic([long]), copy_for).notes["a"]
    assert len(note.strengths) == MAX_ITEMS and len(note.strengths[0]) <= MAX_ITEM_CHARS
    assert note.strengths[0].startswith("a b c ") and "\x00" not in note.strengths[0]
    assert note.weaknesses == () and len(note.caption) == MAX_CAPTION_CHARS


def test_a_failing_provider_is_not_asked_again_after_three_failures_in_a_row() -> None:
    bad = Critique(ok=False, error="down: fake outage")
    critic = ScriptedCritic([bad])
    result = critique_finalists(ids(8), critic, copy_for)
    assert result.notes == {} and result.failed == 8 and len(critic.critique_calls) == 3


def test_a_raising_provider_and_an_unreadable_copy_cost_one_finalist_each() -> None:
    good = Critique(ok=True, strengths=("fine",), provider="fake", model="m", prompt_version="critique-1")
    critic = ScriptedCritic([RuntimeError("boom"), good])

    def loader(photo_id: str) -> bytes:
        if photo_id == "b":
            raise OSError("unreadable")
        return copy_for(photo_id)

    result = critique_finalists(["a", "b", "c"], critic, loader)
    assert set(result.notes) == {"c"} and result.failed == 2 and critic.critique_calls == ["a", "c"]


# --- maximal marginal relevance and tiers -----------------------------------------------------------------------------


def test_lambda_is_075_by_default_and_in_the_shipped_config(config: dict) -> None:
    assert SelectionSettings().mmr_lambda == 0.75 and SelectionSettings().final_size == 25
    assert SelectionSettings.from_mapping(config["selection"]) == SelectionSettings()
    assert TIER_SIZES == (10, 25, 50, 100)


def test_tiers_have_the_four_sizes_and_are_nested() -> None:
    ranked = ids(150)
    selection = select_diverse(ranked, random_vectors(ranked))
    assert [len(selection.tiers[size]) for size in TIER_SIZES] == [10, 25, 50, 100]
    assert len(selection.sequence) == 150 and len(selection.final) == 25 and selection.final == selection.tiers[25]
    for small, large in zip(TIER_SIZES, TIER_SIZES[1:]):
        assert set(selection.tiers[small]) < set(selection.tiers[large])
        assert selection.tiers[large][:small] == selection.tiers[small]
    assert selection.tier_of(selection.sequence[0]) == 10 and selection.tier_of(selection.sequence[10]) == 25
    assert selection.tier_of(selection.sequence[60]) == 100 and selection.tier_of(selection.sequence[120]) is None


def test_the_final_set_size_is_configurable_and_nested_in_the_sequence() -> None:
    ranked = ids(150)
    vectors = random_vectors(ranked)
    selection = select_diverse(ranked, vectors, SelectionSettings(final_size=30))
    assert len(selection.final) == 30 and selection.final == selection.sequence[:30]
    assert set(selection.tiers[25]) < set(selection.final) < set(selection.tiers[50])
    seven = select_diverse(ranked, vectors, SelectionSettings.from_mapping({"final_size": 7}))
    assert seven.final == seven.tiers[10][:7]
    assert SelectionSettings.from_mapping({"final_size": "x"}).final_size == 25


def test_tiers_are_clamped_when_the_pool_is_small() -> None:
    def sizes(count: int) -> list[int]:
        return [len(select_diverse(ids(count), random_vectors(ids(count))).tiers[size]) for size in TIER_SIZES]

    assert sizes(30) == [10, 25, 30, 30]
    assert sizes(3) == [3, 3, 3, 3]
    empty = select_diverse([], {})
    assert empty.sequence == () and empty.final == () and all(empty.tiers[size] == () for size in TIER_SIZES)
    assert len(select_diverse(ids(5), random_vectors(ids(5))).final) == 5  # the default 25 asks for more than exist


def test_only_the_top_of_the_ranking_enters_the_pool() -> None:
    ranked = ids(300)
    assert len(select_diverse(ranked, random_vectors(ranked)).sequence) == 200
    assert SelectionSettings.from_mapping({"pool_size": 5, "final_size": 3}).pool_size == 100  # never below a tier
    assert SelectionSettings.from_mapping({"pool_size": 10**9}).pool_size == 500
    assert len(mmr_sequence(ranked, {}, 0.75, 20)) == 20


def test_a_look_alike_is_pushed_back_in_favor_of_a_different_photo() -> None:
    ranked = ["a1", "a2", "b1"] + [f"o{k}" for k in range(9)]
    unit = np.eye(12, dtype=np.float32)
    vectors = {"a1": unit[0], "a2": unit[0], "b1": unit[1], **{f"o{k}": unit[2 + k] for k in range(9)}}
    diverse = mmr_sequence(ranked, vectors, 0.75, 100)
    assert diverse[:5] == ("a1", "b1", "o0", "o1", "a2")
    assert mmr_sequence(ranked, vectors, 1.0, 100) == tuple(ranked)  # lambda 1 is the rank alone
    assert mmr_sequence(ranked, {}, 0.75, 100) == tuple(ranked)  # without embeddings nothing counts as alike
    odd = {**vectors, "a2": np.zeros(12, dtype=np.float32), "b1": np.ones(3, dtype=np.float32)}
    assert mmr_sequence(ranked, odd, 0.75, 100) == tuple(ranked)  # a zero vector and another size are never compared


def test_the_selection_rejects_a_bad_lambda_and_a_repeated_photo() -> None:
    for bad in (-0.1, 1.5, float("nan")):
        with pytest.raises(ValueError):
            mmr_sequence(["a"], {}, bad, 10)
    with pytest.raises(ValueError):
        mmr_sequence(["a", "a"], {}, 0.75, 10)
    assert SelectionSettings.from_mapping({"mmr_lambda": 7}).mmr_lambda == 1.0
    assert cut_tiers(["a", "b"], 1).final == ("a",)


# --- reranking twice gives the same result ----------------------------------------------------------------------------


def test_reranking_twice_gives_the_same_result_and_no_new_call(conn) -> None:
    photos, quality = library(40), shuffled_quality(40)
    judge = FakeJudge(quality)
    first = curate(photos, judge, conn)
    calls = len(judge.compare_calls)
    second = curate(photos, judge, conn)
    assert len(judge.compare_calls) == calls and second.tournament.provider_calls == 0
    assert shape(first) == shape(second)
    assert first.critique.notes.keys() == second.critique.notes.keys()


def test_ties_and_input_order_do_not_change_the_result(conn) -> None:
    same = np.ones(4, dtype=np.float32)
    photos = [CurationInput(photo_id, 0.5, None, None, same) for photo_id in ids(30)]  # equal scores, equal looks
    judge = FakeJudge()  # equal quality: every pair is a tie
    first = curate(photos, judge, conn)
    again = curate(photos, judge, conn)
    reversed_input = curate(list(reversed(photos)), judge, conn)
    shuffled = list(photos)
    random.Random(5).shuffle(shuffled)
    assert shape(first) == shape(again) == shape(reversed_input) == shape(curate(shuffled, judge, conn))
    assert [e.photo_id for e in first.ranking] == ids(30)  # ties fall back to the ID
    assert select_diverse(ids(30), {}) == select_diverse(ids(30), {})


def test_without_a_provider_the_prescore_decides_and_the_run_still_selects(conn) -> None:
    result = curate(library(20), None, conn)
    assert result.tournament.skipped and result.tournament.reason == "no_provider"
    assert [e.photo_id for e in result.ranking] == ids(20) and {e.source for e in result.ranking} == {"prescore"}
    assert len(result.selection.final) == 5 and result.critique.reason == "no_provider"


def test_photos_beyond_the_shortlist_and_unscored_photos_follow_in_order(conn) -> None:
    photos = library(20) + [CurationInput("zz-unscored", None), CurationInput("aa-unscored", None)]
    result = curate(photos, FakeJudge(shuffled_quality(20)), conn)
    order = [e.photo_id for e in result.ranking]
    assert order[16:20] == ids(20)[16:]  # beyond the shortlist of 16, by prescore
    assert order[-2:] == ["aa-unscored", "zz-unscored"] and result.unscored == 2
    assert [e.rank for e in result.ranking] == list(range(1, 23))
    assert {e.source for e in result.ranking[:16]} == {"pairwise"}
    with pytest.raises(ValueError):
        curate(photos + [photos[0]], None, conn)


def test_an_alternate_of_a_group_is_listed_on_its_representative(conn) -> None:
    photos = [CurationInput("a1", 0.9, 1), CurationInput("a2", 0.5, 1), CurationInput("b1", 0.8, 2)]
    result = curate(photos, None, conn)
    assert result.by_id["a1"].alternates == ("a2",) and result.by_id["b1"].alternates == ()
    assert [e.photo_id for e in result.ranking] == ["a1", "b1", "a2"]


# --- prescore and the library -----------------------------------------------------------------------------------------


def test_the_prescore_follows_the_documented_formula() -> None:
    assert prescore(0.8, 0.5, 0.05) == pytest.approx(0.6 * 0.8 + 0.4 * 0.5 - 0.05)
    assert prescore(None, 0.5) == pytest.approx(0.5) and prescore(0.9, None, 0.1) == pytest.approx(0.8)
    assert prescore(None, None, 0.1) is None and prescore(float("nan"), None) is None
    assert prescore(1.0, 1.0, 5.0) == pytest.approx(0.75)  # the penalty is capped
    assert prescore(1.0, 1.0, float("nan")) == pytest.approx(1.0) and prescore(1.0, 1.0, -1.0) == pytest.approx(1.0)


def test_the_stored_library_gives_scores_tags_groups_and_skips_rejected_photos(conn) -> None:
    kept = Photo(id="b" * 64, file_path="/p/b.jpg", file_name="b.jpg", status="accepted", cluster_id=3, people_penalty=0.05)
    first = Photo(id="a" * 64, file_path="/p/a.jpg", file_name="a.jpg", status="accepted")
    rejected = Photo(id="c" * 64, file_path="/p/c.jpg", file_name="c.jpg", status="rejected")
    for photo in (kept, first, rejected):
        db.upsert_photo(conn, photo)
    db.save_neural_scores(conn, kept.id, "m", quality=0.5, aesthetic=0.9)
    db.save_content_tag(conn, kept.id, "landscape", 0.3, "m")
    db.save_embedding(conn, kept.id, np.array([1.0, 0.0], dtype=np.float32), "m")

    stored = load_stored_library(conn)
    assert [item.photo_id for item in stored.inputs] == [first.id, kept.id]  # sorted by ID, rejected left out
    by_id = {item.photo_id: item for item in stored.inputs}
    assert by_id[kept.id].prescore == pytest.approx(0.6 * 0.9 + 0.4 * 0.5 - 0.05)
    assert by_id[kept.id].group_id == 3 and by_id[kept.id].tag == "landscape"
    assert by_id[kept.id].embedding.tolist() == [1.0, 0.0]
    assert by_id[first.id].prescore is None and by_id[first.id].embedding is None
    assert stored.file_names[kept.id] == "b.jpg" and stored.file_paths[kept.id] == "/p/b.jpg"


def test_the_copy_loader_reads_the_working_copy_and_returns_a_clean_copy(data_config: dict) -> None:
    path = working_copy_path(data_config, "abc")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1500, 1000), (120, 80, 40)).save(path, "JPEG")
    load = working_copy_loader(data_config)
    check_copy(load("abc"))  # a JPEG within 1,024 px and without metadata
    with pytest.raises(OSError):
        load("missing")


# --- export -------------------------------------------------------------------------------------------------------------


def write_originals(folder: Path, names: list[str]) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    sources = {}
    for index, name in enumerate(names):
        path = folder / name
        path.write_bytes(f"original {index}".encode())
        sources[f"id{index}"] = str(path)
    return sources


def digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_exports_are_numbered_copies_and_originals_stay_unchanged(tmp_path: Path) -> None:
    sources = write_originals(tmp_path / "photos", ["sunset.jpg", "IMG_0001.png", "dock.jpeg"])
    before = {pid: (digest(path), Path(path).stat().st_mtime_ns) for pid, path in sources.items()}
    names = {pid: Path(path).name for pid, path in sources.items()}
    plan = plan_export(["id2", "id0", "id1"], names)
    assert [item.name for item in plan] == ["001_dock.jpeg", "002_sunset.jpg", "003_IMG_0001.png"]

    result = export_copies(plan, sources, tmp_path / "out" / "final_set")
    assert result.missing == [] and result.copied == {"id2": "001_dock.jpeg", "id0": "002_sunset.jpg", "id1": "003_IMG_0001.png"}
    for item in plan:
        assert digest(tmp_path / "out" / "final_set" / item.name) == before[item.photo_id][0]
    assert {pid: (digest(path), Path(path).stat().st_mtime_ns) for pid, path in sources.items()} == before
    assert sorted(p.name for p in (tmp_path / "photos").iterdir()) == ["IMG_0001.png", "dock.jpeg", "sunset.jpg"]


def test_the_number_widens_past_999_and_equal_names_stay_unique() -> None:
    plan = plan_export([f"id{i}" for i in range(1000)], {})
    assert plan[0].name == "0001_photo" and plan[999].name == "1000_photo" and len({i.name for i in plan}) == 1000
    twins = plan_export(["x", "y"], {"x": "IMG.jpg", "y": "IMG.jpg"})
    assert [i.name for i in twins] == ["001_IMG.jpg", "002_IMG.jpg"]


def test_original_names_are_made_safe_as_one_path_component() -> None:
    assert "/" not in safe_original_name("../../etc/passwd") and "\\" not in safe_original_name("..\\evil.jpg")
    assert safe_original_name("") == "photo" and safe_original_name("..") == "photo"
    assert safe_original_name("a\nb.jpg") == "a_b.jpg"
    long = safe_original_name("n" * 400 + ".jpeg")
    assert len(long) == 200 and long.endswith(".jpeg")
    assert safe_original_name("holiday ☀.jpg") == "holiday ☀.jpg"


def test_an_export_never_lands_in_a_folder_of_originals(tmp_path: Path) -> None:
    sources = write_originals(tmp_path / "photos", ["a.jpg", "b.jpg"])
    before = sorted(p.name for p in (tmp_path / "photos").iterdir())
    plan = plan_export(["id0", "id1"], {"id0": "a.jpg", "id1": "b.jpg"})
    with pytest.raises(ValueError):
        export_copies(plan, sources, tmp_path / "photos")
    assert sorted(p.name for p in (tmp_path / "photos").iterdir()) == before


def test_an_export_folder_that_is_a_link_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "link"
    # The folder is reported as a link without creating one, so the check runs on hosts that cannot make symbolic links.
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == link or real_is_symlink(self))
    sources = write_originals(tmp_path / "photos", ["a.jpg"])
    with pytest.raises(ValueError):
        export_copies(plan_export(["id0"], {"id0": "a.jpg"}), sources, link)
    assert list(target.iterdir()) == []


def test_a_missing_source_is_listed_and_a_new_export_replaces_only_the_old_numbered_files(tmp_path: Path) -> None:
    sources = write_originals(tmp_path / "photos", ["a.jpg", "b.jpg"])
    names = {"id0": "a.jpg", "id1": "b.jpg", "gone": "gone.jpg"}
    dest = tmp_path / "final_set"
    export_copies(plan_export(["id0", "id1"], names), sources, dest)
    (dest / "notes.txt").write_text("mine")
    again = export_copies(plan_export(["gone", "id1"], names), {**sources, "gone": str(tmp_path / "nowhere.jpg")}, dest)
    assert again.missing == ["gone"] and again.copied == {"id1": "002_b.jpg"}
    assert sorted(p.name for p in dest.iterdir()) == ["002_b.jpg", "notes.txt"]  # the earlier numbered files went
    assert (tmp_path / "photos" / "a.jpg").read_bytes() == b"original 0"


# --- reports: model text is always escaped -------------------------------------------------------------------------------

HOSTILE = {
    "strengths": ("<script>alert(1)</script>", '"><img src=x onerror=alert(1)>'),
    "weaknesses": ('=HYPERLINK("http://x.invalid","y")', "+cmd|' /C calc'!A0"),
    "caption": "@SUM(1)\n<b>bold</b> & more",
}


class TagCollector(HTMLParser):
    """Records every start tag, every attribute name and the visible text of a page."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[str] = []
        self.text = ""

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append(tag)
        self.attributes += [name for name, _ in attrs]

    def handle_data(self, data) -> None:
        self.text += data


def report_for(tmp_path: Path, conn, texts=HOSTILE, provider=True):
    photos = library(12)
    judge = FakeJudge(shuffled_quality(12), texts=texts) if provider else None
    result = curate(photos, judge, conn, selection=SelectionSettings(final_size=3))
    names = {item.photo_id: f"{item.photo_id}.jpg" for item in photos}
    names[result.selection.final[0]] = '<i onclick="x()">shot</i>.jpg'
    exported = {pid: f"{n:03d}_{names[pid]}" for n, pid in enumerate(result.selection.final, start=1)}
    rows = build_rows(result, names, exported)
    return result, rows, write_reports(rows, result, tmp_path / "reports")


def test_model_text_with_markup_is_escaped_in_the_html(tmp_path: Path, conn) -> None:
    result, rows, (_, html_path) = report_for(tmp_path, conn)
    page = html_path.read_text(encoding="utf-8")
    parser = TagCollector()
    parser.feed(page)

    assert html_path.name == HTML_NAME
    assert not {"script", "img", "b", "i", "a", "iframe", "link"} & set(parser.tags)
    assert not [name for name in parser.attributes if name.startswith("on")]
    assert "<script" not in page.lower() and "<img" not in page.lower() and 'onclick="' not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "&quot;&gt;&lt;img" in page and "&lt;b&gt;bold&lt;/b&gt; &amp; more" in page
    assert "&lt;i onclick=&quot;x()&quot;&gt;shot&lt;/i&gt;.jpg" in page  # a file name is escaped too
    assert "<script>alert(1)</script>" in parser.text  # the text survives as text, not as markup
    assert "Content-Security-Policy" in page and "default-src 'none'" in page


def test_model_text_with_markup_is_neutralized_in_the_csv(tmp_path: Path, conn) -> None:
    _, rows, (csv_path, _) = report_for(tmp_path, conn)
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        table = list(csv.reader(handle))
    assert csv_path.name == CSV_NAME and tuple(table[0]) == CSV_COLUMNS and len(table) == 1 + len(rows)
    assert all(len(line) == len(CSV_COLUMNS) for line in table)  # a line break in a cell never splits a row
    text_columns = [CSV_COLUMNS.index(name) for name in ("file_name", "export_name", "strengths", "weaknesses", "caption")]
    for line in table[1:]:
        for column in text_columns:
            assert not line[column].startswith(("=", "+", "-", "@", "\t", "\r"))
    final_line = next(line for line in table[1:] if line[CSV_COLUMNS.index("final_set")] == "yes")
    assert final_line[CSV_COLUMNS.index("weaknesses")].startswith("'=HYPERLINK(")
    assert final_line[CSV_COLUMNS.index("caption")] == "'@SUM(1) <b>bold</b> & more"
    assert csv_safe("=1+1") == "'=1+1" and csv_safe("plain") == "plain" and csv_safe("a\r\nb") == "a b"


def test_the_report_lists_tiers_ranks_and_the_way_the_rank_was_reached(tmp_path: Path, conn) -> None:
    result, rows, (csv_path, html_path) = report_for(tmp_path, conn)
    assert [row.position for row in rows] == list(range(1, 13)) and len(rows) == len(result.selection.sequence)
    assert [row.in_final_set for row in rows] == [True] * 3 + [False] * 9
    assert rows[0].tier == 10 and rows[0].rank == result.by_id[result.selection.sequence[0]].rank
    assert {row.rank_source for row in rows} == {"pairwise"} and all(row.strength is not None for row in rows)
    assert "Judged pairwise" in html_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in csv_path.read_text(encoding="utf-8-sig")  # file names only, never paths

    _, plain_rows, (_, plain_html) = report_for(tmp_path / "plain", conn, provider=False)
    page = plain_html.read_text(encoding="utf-8")
    assert "the prescore decided (no_provider)" in page and "No critique was written (no_provider)" in page
    assert all(row.strength is None and row.caption == "" for row in plain_rows)
