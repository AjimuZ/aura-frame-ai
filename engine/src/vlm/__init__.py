"""Model stage of a run for AuraFrame AI.

The runner checks the local vision provider (engine/src/providers) before the candidate stage;
schema holds the legacy single-photo review prompt and parser that remain until package P04
replaces them with pairwise comparisons.
"""

from .runner import VlmRunner, judge_candidates

__all__ = ["VlmRunner", "judge_candidates"]
