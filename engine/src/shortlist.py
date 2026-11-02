"""Shortlist: the top K group representatives by prescore, with a cap per content tag.

Only the shortlist reaches the pairwise judge, never the whole collection. A group is represented by its
best photo; the others stay listed as alternates. Nothing here reads an image or a path.
"""

import math
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class Candidate:
    """One photo that may enter the shortlist.

    `photo_id` is the content hash. `prescore` is any finite number where higher is better (only the order and the
    spread matter). `group_id` is the grouping result; None or a negative value means the photo is in no group.
    `tag` is the content tag, or None when unknown.
    """

    photo_id: str
    prescore: float
    group_id: int | None = None
    tag: str | None = None

    # Rejects a prescore that is not a finite number, so no ordering depends on NaN.
    def __post_init__(self) -> None:
        if isinstance(self.prescore, bool) or not math.isfinite(self.prescore):
            raise ValueError("a prescore must be a finite number")


@dataclass(frozen=True)
class Shortlist:
    """The chosen representatives (best prescore first) and what was left out.

    `alternates` maps each member to the other photos of its group, best first. `skipped_by_tag` counts the
    representatives that were passed over because their tag already had `max_per_tag` members.
    """

    members: tuple[Candidate, ...]
    alternates: dict[str, tuple[str, ...]] = field(default_factory=dict)
    skipped_by_tag: int = 0


# Orders candidates best first; equal prescores fall back to the id, so the order never depends on the input.
def _best_first(candidates: Sequence[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda c: (-c.prescore, c.photo_id))


# Returns the per-tag limit for a shortlist of `size`: `share` of it, rounded up, and at least 1.
def tag_limit(size: int, share: float) -> int:
    return max(1, math.ceil(size * share))


# Picks at most `size` group representatives, best prescore first, taking at most `max_per_tag` per content tag.
# A photo without a tag is not limited. The list is shorter than `size` when the groups or the tag limit run out:
# the cap is never relaxed to fill it. The result does not depend on the order of `candidates`.
# Raises ValueError for a size or limit below 1.
def select_shortlist(candidates: Sequence[Candidate], size: int, max_per_tag: int) -> Shortlist:
    if size < 1 or max_per_tag < 1:
        raise ValueError("the shortlist size and the per-tag limit must be at least 1")
    groups: dict[object, list[Candidate]] = {}
    for candidate in candidates:
        grouped = candidate.group_id is not None and candidate.group_id >= 0
        groups.setdefault(candidate.group_id if grouped else ("single", candidate.photo_id), []).append(candidate)
    ranked_groups = [_best_first(members) for members in groups.values()]
    representatives = _best_first([members[0] for members in ranked_groups])
    others = {members[0].photo_id: tuple(c.photo_id for c in members[1:]) for members in ranked_groups}

    chosen: list[Candidate] = []
    per_tag: dict[str, int] = {}
    skipped = 0
    for candidate in representatives:
        if len(chosen) >= size:
            break
        if candidate.tag is not None:
            if per_tag.get(candidate.tag, 0) >= max_per_tag:
                skipped += 1
                continue
            per_tag[candidate.tag] = per_tag.get(candidate.tag, 0) + 1
        chosen.append(candidate)
    return Shortlist(tuple(chosen), {c.photo_id: others[c.photo_id] for c in chosen}, skipped)
