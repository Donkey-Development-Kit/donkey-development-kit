"""llm/ — governed model access, framework-free surface (BG §1.8, BG §1.1)."""

from .catalog import ModelCapabilities, ModelHandle, heuristic_capabilities
from .client import LLMClient

__all__ = ["LLMClient", "ModelCapabilities", "ModelHandle", "heuristic_capabilities"]
