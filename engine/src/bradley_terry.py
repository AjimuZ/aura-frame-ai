"""Bradley-Terry ranking of pairwise outcomes, with the prescore as a weak Gaussian prior.

Each photo has a strength theta (a log-odds scale). The model says photo i beats j with probability
sigmoid(theta_i - theta_j). The fit is a maximum a posteriori estimate: the prior pulls each strength toward its
standardized prescore with a small weight, so an unbeaten or winless photo stays finite, a photo with no outcome
keeps the prescore order, and a few decisive outcomes override the prescore. The uncertainty of each strength is its
standard error under a Laplace approximation (the inverse Hessian at the optimum). Only outcomes rank; no absolute
vote enters.
"""

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

# Newton's method on a strictly convex objective; this is far more than it ever needs.
MAX_ITERATIONS = 100
TOLERANCE = 1e-9
MAX_STEP = 2.0


@dataclass(frozen=True)
class Outcome:
    """The result of one pair. `score` is the share of the pair won by `first_id`: 1 win, 0.5 tie, 0 loss."""

    first_id: str
    second_id: str
    score: float


@dataclass(frozen=True)
class Rating:
    """A photo's place in the ranking. `rank` starts at 1.

    `strength` is the fitted log-odds value and `uncertainty` its standard error (None when no model ranked it, so
    the prescore decided). `source` is "pairwise" or "prescore".
    """

    photo_id: str
    rank: int
    strength: float
    uncertainty: float | None
    source: str = "pairwise"


# Returns the prescores standardized to mean 0 and standard deviation 1; all zeros when they do not differ.
def _standardized(values: np.ndarray) -> np.ndarray:
    spread = float(values.std())
    if spread < 1e-12:
        return np.zeros_like(values)
    return (values - values.mean()) / spread


# Ranks `prescores` (photo id -> prescore) with `outcomes`, best first. `prior_scale` is the strength gap that one
# standard deviation of prescore stands for, and `prior_precision` how firmly the prior holds (both log-odds
# units; the defaults are weak). An outcome that names an unknown photo or a score outside 0..1 raises ValueError.
# Equal strengths are ordered by prescore, then id, so the result is deterministic.
def fit_bradley_terry(
    prescores: Mapping[str, float],
    outcomes: Sequence[Outcome],
    prior_scale: float = 0.5,
    prior_precision: float = 0.5,
) -> list[Rating]:
    if prior_precision <= 0 or prior_scale < 0:
        raise ValueError("the prior precision must be positive and the prior scale not negative")
    ids = sorted(prescores)
    if not ids:
        return []
    index = {photo_id: i for i, photo_id in enumerate(ids)}
    size = len(ids)
    wins = np.zeros((size, size))  # wins[i, j]: the share of the i-versus-j pairs that i won
    for outcome in outcomes:
        if outcome.first_id not in index or outcome.second_id not in index or outcome.first_id == outcome.second_id:
            raise ValueError("an outcome must name two different known photos")
        if not 0.0 <= outcome.score <= 1.0:  # also false for NaN
            raise ValueError("an outcome score must be between 0 and 1")
        i, j = index[outcome.first_id], index[outcome.second_id]
        wins[i, j] += outcome.score
        wins[j, i] += 1.0 - outcome.score
    games = wins + wins.T

    raw = np.array([float(prescores[photo_id]) for photo_id in ids])
    prior = prior_scale * _standardized(raw)
    theta = prior.copy()
    for _ in range(MAX_ITERATIONS):
        diff = theta[:, None] - theta[None, :]
        p = 1.0 / (1.0 + np.exp(-diff))
        gradient = (wins - games * p).sum(axis=1) - prior_precision * (theta - prior)
        weights = games * p * (1.0 - p)
        curvature = np.diag(weights.sum(axis=1)) - weights + prior_precision * np.eye(size)
        step = np.clip(np.linalg.solve(curvature, gradient), -MAX_STEP, MAX_STEP)
        theta = theta + step
        if float(np.abs(step).max()) < TOLERANCE:
            break

    diff = theta[:, None] - theta[None, :]
    p = 1.0 / (1.0 + np.exp(-diff))
    weights = games * p * (1.0 - p)
    curvature = np.diag(weights.sum(axis=1)) - weights + prior_precision * np.eye(size)
    errors = np.sqrt(np.diag(np.linalg.inv(curvature)))
    order = sorted(range(size), key=lambda i: (-round(float(theta[i]), 9), -raw[i], ids[i]))
    return [
        Rating(ids[i], rank, float(theta[i]), float(errors[i]))
        for rank, i in enumerate(order, start=1)
    ]
