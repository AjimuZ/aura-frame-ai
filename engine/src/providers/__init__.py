"""Vision providers: the contract and the local llama-server implementation."""

from .contract import (
    Comparison,
    Critique,
    Prompt,
    ProviderHealth,
    ProviderSettings,
    ProviderStatus,
    VisionProvider,
)
from .fallback import GuardedProvider, compare_or_prescore, prescore_comparison
from .images import prepare_copy
from .llama_server import LlamaServerProvider
from .prompts import COMPARE_PROMPT, CRITIQUE_PROMPT

__all__ = [
    "COMPARE_PROMPT",
    "CRITIQUE_PROMPT",
    "Comparison",
    "Critique",
    "GuardedProvider",
    "LlamaServerProvider",
    "Prompt",
    "ProviderHealth",
    "ProviderSettings",
    "ProviderStatus",
    "VisionProvider",
    "compare_or_prescore",
    "prepare_copy",
    "prescore_comparison",
]
