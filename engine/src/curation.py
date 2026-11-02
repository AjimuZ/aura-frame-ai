"""Curation stage: prescore, shortlist, tournament, diversity-aware selection and finalist critique in one call.

The order of the work is the contract. The rank is fixed by the tournament (or the prescore without a judge), the
selection is cut from that rank, and only then are the finalists critiqued, so the critique cannot change either.
Everything is a pure function of the inputs and the verdict cache, so running it twice gives the same result.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from . import db
from .critique import CritiqueResult, critique_finalists
from .providers import COMPARE_PROMPT, CRITIQUE_PROMPT, Prompt, VisionProvider, prepare_copy
from .selection import Selection, SelectionSettings, select_diverse
from .shortlist import Candidate, select_shortlist
from .tournament import TournamentResult, TournamentSettings, run_tournament
from .utils import working_copy_path

# The prescore weights from docs/pipeline.md: 0.6 aesthetic and 0.4 quality, minus the people penalty.
AESTHETIC_WEIGHT = 0.6
QUALITY_WEIGHT = 0.4
# The people penalty is on the 0 to 1 scale of the prescore and capped as in the face settings.
MAX_PEOPLE_PENALTY = 0.25


@dataclass(frozen=True)
class CurationInput:
    """What the stage knows about one photo. Only IDs, numbers and vectors: no path, no file name, no pixels.

    `prescore` is None when the photo has no usable score. `group_id` is the grouping result (negative or None for
    no group), `tag` the content tag, `embedding` the SigLIP 2 vector used only to tell look-alikes apart.
    """

    photo_id: str
    prescore: float | None
    group_id: int | None = None
    tag: str | None = None
    embedding: np.ndarray | None = None


@dataclass(frozen=True)
class RankedPhoto:
    """One place in the full ranking. `rank` starts at 1.

    `source` is "pairwise" (the tournament fitted it), "prescore" (the prescore decided) or "unscored" (no score was
    known, so the photo follows every scored one in ID order). `strength` and `uncertainty` are Bradley-Terry values
    in log-odds units; for "prescore" the strength is the prescore itself and the uncertainty is None.
    """

    photo_id: str
    rank: int
    source: str
    strength: float | None
    uncertainty: float | None
    prescore: float | None
    group_id: int | None
    tag: str | None
    alternates: tuple[str, ...] = ()


@dataclass
class CurationResult:
    """The ranking (best first), the selection cut from it, and the stage results that produced them."""

    ranking: list[RankedPhoto]
    selection: Selection
    tournament: TournamentResult
    critique: CritiqueResult
    unscored: int = 0
    by_id: dict[str, RankedPhoto] = field(default_factory=dict)


# Returns the prescore of a photo: 0.6 * aesthetic + 0.4 * quality - people penalty, on the 0 to 1 scale. A value that
# is unknown is left out and the weights of the known ones are renormalized, so nothing is invented; with neither
# known the result is None. A penalty that is not a finite number counts as 0 and is capped at MAX_PEOPLE_PENALTY.
def prescore(aesthetic: float | None, quality: float | None, people_penalty: float = 0.0) -> float | None:
    parts = [
        (weight, float(value))
        for weight, value in ((AESTHETIC_WEIGHT, aesthetic), (QUALITY_WEIGHT, quality))
        if value is not None and math.isfinite(float(value))
    ]
    if not parts:
        return None
    total = sum(weight for weight, _ in parts)
    base = sum(weight * value for weight, value in parts) / total
    penalty = float(people_penalty) if math.isfinite(float(people_penalty)) else 0.0
    return base - min(max(penalty, 0.0), MAX_PEOPLE_PENALTY)


# Builds the full ranking: the tournament order for the shortlist, then every other scored photo by prescore, then the
# unscored ones by ID. Ranks run from 1 without gaps.
def _full_ranking(
    inputs: Mapping[str, CurationInput],
    tournament: TournamentResult,
    alternates: Mapping[str, tuple[str, ...]],
) -> list[RankedPhoto]:
    ranking: list[RankedPhoto] = []

    def add(photo_id: str, source: str, strength: float | None, uncertainty: float | None) -> None:
        item = inputs[photo_id]
        ranking.append(
            RankedPhoto(
                photo_id, len(ranking) + 1, source, strength, uncertainty, item.prescore,
                item.group_id, item.tag, alternates.get(photo_id, ()),
            )  # fmt: skip
        )

    for rating in tournament.ratings:
        add(rating.photo_id, rating.source, rating.strength, rating.uncertainty)
    placed = {rating.photo_id for rating in tournament.ratings}
    rest = [item for item in inputs.values() if item.photo_id not in placed]
    for item in sorted((i for i in rest if i.prescore is not None), key=lambda i: (-i.prescore, i.photo_id)):  # type: ignore[operator]
        add(item.photo_id, "prescore", item.prescore, None)
    for item in sorted((i for i in rest if i.prescore is None), key=lambda i: i.photo_id):
        add(item.photo_id, "unscored", None, None)
    return ranking


# Runs the whole curation over `photos`. A photo with no usable prescore is never shortlisted but still ranks after
# the scored ones. `copy_for(photo_id)` returns the prepared anonymous copy; it is called only for a verdict that is
# not cached and for a finalist's critique. With `provider` None the prescore decides and no critique is written.
# `with_critique=False` leaves the finalists without a note; the ranking and the selection are the same either way.
# A photo ID may appear only once (ValueError otherwise).
def run_curation(
    photos: Sequence[CurationInput],
    provider: VisionProvider | None,
    conn: Any,
    copy_for: Callable[[str], bytes],
    provider_name: str,
    model: str,
    tournament_settings: TournamentSettings | None = None,
    selection_settings: SelectionSettings | None = None,
    compare_prompt: Prompt = COMPARE_PROMPT,
    critique_prompt: Prompt = CRITIQUE_PROMPT,
    with_critique: bool = True,
) -> CurationResult:
    tournament_settings = tournament_settings or TournamentSettings()
    selection_settings = selection_settings or SelectionSettings()
    inputs = {item.photo_id: item for item in photos}
    if len(inputs) != len(photos):
        raise ValueError("a photo can appear only once in the curation input")

    scored = [
        Candidate(item.photo_id, item.prescore, item.group_id, item.tag)
        for item in inputs.values()
        if item.prescore is not None
    ]
    shortlist = select_shortlist(scored, tournament_settings.shortlist_size, tournament_settings.max_per_tag)
    tournament = run_tournament(
        shortlist.members, provider, conn, copy_for, provider_name, model, compare_prompt, tournament_settings
    )
    ranking = _full_ranking(inputs, tournament, shortlist.alternates)

    embeddings = {item.photo_id: item.embedding for item in inputs.values() if item.embedding is not None}
    selection = select_diverse([entry.photo_id for entry in ranking], embeddings, selection_settings)

    # The critique runs last and receives IDs only, so it has no way to change the ranking or the selection.
    if with_critique:
        critique = critique_finalists(selection.final, provider, copy_for, critique_prompt)
    else:
        critique = CritiqueResult({}, skipped=True, reason="disabled")
    return CurationResult(
        ranking, selection, tournament, critique,
        unscored=sum(1 for entry in ranking if entry.source == "unscored"),
        by_id={entry.photo_id: entry for entry in ranking},
    )  # fmt: skip


@dataclass(frozen=True)
class StoredLibrary:
    """The curation input read from the library database, with the names and paths kept apart from it.

    `file_names` and `file_paths` exist for the export and the report only; they never reach a provider.
    """

    inputs: list[CurationInput]
    file_names: dict[str, str]
    file_paths: dict[str, str]


# Reads the photos the gate kept, with their scores, tags, groups and embeddings, from the library database.
# A rejected photo is left out. The inputs are sorted by ID, so the order never depends on the database.
def load_stored_library(conn: Any) -> StoredLibrary:
    scores = db.load_neural_scores(conn)
    tags = db.load_content_tags(conn)
    vectors = db.load_embeddings(conn)
    inputs: list[CurationInput] = []
    names: dict[str, str] = {}
    paths: dict[str, str] = {}
    for photo in sorted(db.load_photos(conn), key=lambda p: p.id):
        if photo.status == "rejected":
            continue
        row = scores.get(photo.id) or {}
        inputs.append(
            CurationInput(
                photo.id,
                prescore(row.get("aesthetic"), row.get("quality"), photo.people_penalty),
                photo.cluster_id,
                (tags.get(photo.id) or {}).get("tag"),
                vectors.get(photo.id),
            )
        )
        names[photo.id] = photo.file_name
        paths[photo.id] = photo.file_path
    return StoredLibrary(inputs, names, paths)


# Returns a loader of anonymous copies built from the upright working copy of each photo, never from the original.
# The loader raises OSError when a working copy is missing or unreadable, which the stages treat as "no verdict".
def working_copy_loader(config: dict[str, Any]) -> Callable[[str], bytes]:
    def load(photo_id: str) -> bytes:
        return prepare_copy(working_copy_path(config, photo_id))

    return load
