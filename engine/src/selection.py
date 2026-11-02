"""Diversity-aware selection: maximal marginal relevance over the ranking, and the nested tiers it yields.

The ranking (best first) is the only relevance signal; the embeddings only measure how alike two photos look. Each
step takes the photo that best trades its rank against its similarity to what is already chosen. Because the
sequence is greedy, every tier is a prefix of it: Top 10 sits inside Top 25, which sits inside Top 50 and Top 100,
and the configurable final set is a prefix too. Nothing here reads an image or a path, and equal inputs always give
the same sequence, whatever the input order.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from .providers.contract import _bounded

# The tiers the product offers. They are fixed; only the final set size is configurable.
TIER_SIZES = (10, 25, 50, 100)
# Upper bound for the final set and for the pool, so no configuration asks for an unbounded selection.
SIZE_BOUNDS = (1, 500)


@dataclass(frozen=True)
class SelectionSettings:
    """Selection limits. Build it with `from_mapping`, which clamps every value.

    `mmr_lambda` weighs rank against novelty (1 is rank only, 0 is novelty only). `final_size` is the size of the
    proposed final set. `pool_size` is how many top-ranked photos may enter the selection; it is never below the
    largest tier, so a tier can always be filled when the collection is large enough.
    """

    mmr_lambda: float = 0.75
    final_size: int = 25
    pool_size: int = 200

    # Builds settings from the `selection` mapping of the engine configuration; unknown keys are ignored.
    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "SelectionSettings":
        raw = values or {}
        d = cls()
        final_size = _bounded(raw.get("final_size"), d.final_size, SIZE_BOUNDS, int)
        pool_size = _bounded(raw.get("pool_size"), d.pool_size, SIZE_BOUNDS, int)
        return cls(
            mmr_lambda=_bounded(raw.get("mmr_lambda"), d.mmr_lambda, (0.0, 1.0), float),
            final_size=final_size,
            pool_size=max(pool_size, final_size, max(TIER_SIZES)),
        )

    # The number of photos the report and the export cover: the largest tier or the final set, whichever is bigger.
    @property
    def listed_size(self) -> int:
        return max(self.final_size, max(TIER_SIZES))


@dataclass(frozen=True)
class Selection:
    """The MMR sequence (first choice first) and the sets cut from it.

    `tiers` maps each tier size to its members, clamped to the number of photos available, so a small collection
    gives the same members for several tiers. `final` is the proposed final set. Every tier and `final` is a prefix
    of `sequence`, so they are nested.
    """

    sequence: tuple[str, ...]
    tiers: dict[int, tuple[str, ...]] = field(default_factory=dict)
    final: tuple[str, ...] = ()

    # Returns the smallest tier that holds the photo, or None when it is in none of them.
    def tier_of(self, photo_id: str) -> int | None:
        for size in TIER_SIZES:
            if photo_id in self.tiers.get(size, ()):
                return size
        return None


# Returns the cosine similarity matrix of the pool, clamped to [0, 1]. A photo with no embedding, a zero vector, or a
# vector of another size than its partner is similar to nothing, so a missing value never counts as a duplicate.
def _similarity_matrix(ids: Sequence[str], embeddings: Mapping[str, np.ndarray]) -> np.ndarray:
    size = len(ids)
    matrix = np.zeros((size, size), dtype=np.float64)
    vectors: list[np.ndarray | None] = []
    for photo_id in ids:
        vector = embeddings.get(photo_id)
        if vector is None:
            vectors.append(None)
            continue
        flat = np.asarray(vector, dtype=np.float64).ravel()
        norm = float(np.linalg.norm(flat))
        vectors.append(flat / norm if flat.size and math.isfinite(norm) and norm > 1e-12 else None)
    for i in range(size):
        for j in range(i + 1, size):
            a, b = vectors[i], vectors[j]
            if a is None or b is None or a.size != b.size:
                continue
            value = float(np.clip(np.dot(a, b), 0.0, 1.0))
            matrix[i, j] = matrix[j, i] = value
    return matrix


# Orders the pool by maximal marginal relevance. `ranked_ids` lists the photos best first; the position in that list
# is the relevance (1 for the first, 0 for the last of the pool), so only the order of the ranking counts, never the
# scale of its scores. Each step maximizes lambda * relevance - (1 - lambda) * (highest similarity to the chosen);
# a tie goes to the better rank. Returns the whole pool in selection order. The result depends only on the ranking
# and the embeddings. Raises ValueError for a repeated id or a lambda outside [0, 1].
def mmr_sequence(
    ranked_ids: Sequence[str],
    embeddings: Mapping[str, np.ndarray],
    mmr_lambda: float,
    pool_size: int,
) -> tuple[str, ...]:
    if not 0.0 <= mmr_lambda <= 1.0:
        raise ValueError("the MMR lambda must be between 0 and 1")
    if len(set(ranked_ids)) != len(ranked_ids):
        raise ValueError("a photo can appear only once in the ranking")
    pool = list(ranked_ids[: max(0, pool_size)])
    count = len(pool)
    if count == 0:
        return ()
    relevance = [1.0 if count == 1 else (count - 1 - position) / (count - 1) for position in range(count)]
    similarity = _similarity_matrix(pool, embeddings)
    chosen: list[int] = []
    remaining = list(range(count))
    highest = np.zeros(count, dtype=np.float64)  # the highest similarity of each photo to the chosen ones
    while remaining:
        # The position in the pool is the rank, so `min` over (-score, position) breaks ties by rank.
        best = min(remaining, key=lambda i: (-(mmr_lambda * relevance[i] - (1.0 - mmr_lambda) * highest[i]), i))
        chosen.append(best)
        remaining.remove(best)
        highest = np.maximum(highest, similarity[best])
    return tuple(pool[i] for i in chosen)


# Cuts the tiers and the final set from the sequence. Every size is clamped to the sequence length.
def cut_tiers(sequence: Sequence[str], final_size: int) -> Selection:
    ordered = tuple(sequence)
    tiers = {size: ordered[:size] for size in TIER_SIZES}
    return Selection(ordered, tiers, ordered[: max(0, final_size)])


# Builds the selection for a ranking (best first): the MMR sequence, the four tiers and the final set.
def select_diverse(
    ranked_ids: Sequence[str],
    embeddings: Mapping[str, np.ndarray],
    settings: SelectionSettings | None = None,
) -> Selection:
    settings = settings or SelectionSettings()
    sequence = mmr_sequence(ranked_ids, embeddings, settings.mmr_lambda, settings.pool_size)
    return cut_tiers(sequence, settings.final_size)
