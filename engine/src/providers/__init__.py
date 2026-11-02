"""Vision providers: the contract, the local llama-server implementation and the opt-in Codex CLI one."""

from .codex import CodexProvider, CodexSettings
from .codex_sidecar import SidecarRunner, sidecar_provider
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
    "CodexProvider",
    "CodexSettings",
    "Comparison",
    "Critique",
    "GuardedProvider",
    "LlamaServerProvider",
    "Prompt",
    "ProviderHealth",
    "ProviderSettings",
    "ProviderStatus",
    "SidecarRunner",
    "VisionProvider",
    "compare_or_prescore",
    "prepare_copy",
    "prescore_comparison",
    "sidecar_provider",
]
