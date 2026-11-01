"""Local vision-language model abstraction for AuraFrame AI.

Provides a provider-agnostic interface (base.VlmClient), a strict JSON schema validator
(schema), a local Ollama implementation (ollama_client), and a runner that coordinates a
primary + fallback model with health/model-presence checks (runner).
"""

from .base import VlmClient, VlmHealth
from .runner import VlmRunner, judge_candidates

__all__ = ["VlmClient", "VlmHealth", "VlmRunner", "judge_candidates"]
