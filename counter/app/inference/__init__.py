"""Model loading & inference helpers shared across cameras."""

from .registry import ModelRegistry, resolve_device

__all__ = ["ModelRegistry", "resolve_device"]
