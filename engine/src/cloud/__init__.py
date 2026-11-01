"""OpenAI Quality Mode: an OPTIONAL, off-by-default parallel cloud review flow.

The local pipeline remains the privacy-first default. When the user explicitly enables this
mode (privacy.allow_cloud_api AND openai.enabled), only resized, EXIF-stripped, anonymized
image copies are sent to the OpenAI API — never originals, file names, paths, or EXIF. A
budget guard caps spend, a rate limiter bounds concurrency, and results are cached locally.
"""

from . import openai_cost_estimator as cost_estimator
from .consensus_ranker import compute_consensus_scores, rank_for_openai
from .openai_budget_guard import BudgetGuard
from .openai_client import (
    FatalApiError,
    MissingApiKeyError,
    ModelNotAvailableError,
    OpenAiClient,
    RetryableError,
)
from .openai_photo_judge import is_cloud_enabled, run_final_judge, run_stage, select_candidates

__all__ = [
    "cost_estimator",
    "compute_consensus_scores",
    "rank_for_openai",
    "BudgetGuard",
    "OpenAiClient",
    "FatalApiError",
    "MissingApiKeyError",
    "ModelNotAvailableError",
    "RetryableError",
    "is_cloud_enabled",
    "run_final_judge",
    "run_stage",
    "select_candidates",
]
