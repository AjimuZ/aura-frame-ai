"""Abstract VLM client interface and health-status types.

Implementations must be fully local. They evaluate dating-photo suitability only and must
never be asked to identify a person (see the prompt in this package).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..utils import Photo


@dataclass
class VlmHealth:
    """Result of probing a local VLM runtime/model."""

    runtime_reachable: bool
    model_available: bool
    message: str


class VlmClient(ABC):
    """Provider-agnostic local vision-language client."""

    model: str

    # Probes runtime reachability and model presence without running inference.
    @abstractmethod
    def health(self) -> VlmHealth: ...

    # Returns a validated review dict for one photo, or None if it could not be produced.
    @abstractmethod
    def review(self, photo: Photo) -> dict[str, Any] | None: ...
