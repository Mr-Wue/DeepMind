"""
Role-based LLM adapter base — migrated from CodeMind base/llm/.

Each subclass registers itself by ``role: ClassVar[str]`` (auto-registry via
__init_subclass__).  Per-temperature caching avoids recreating identical instances.

Usage::

    from llm import get_llm
    llm = get_llm("default", temperature=0)
    llm = get_llm("text2sql")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

load_dotenv()

_ROLE_REGISTRY: dict[str, type["BaseLLM"]] = {}


class BaseLLM(ABC):
    """Role-based LLM adapter.

    Subclasses declare ``role: ClassVar[str]`` and implement ``_make(temperature)``.
    """

    role: ClassVar[str]
    _cache: ClassVar[dict[float, BaseChatModel]]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls._cache = {}
        if hasattr(cls, "role") and isinstance(cls.role, str) and cls.role:
            _ROLE_REGISTRY[cls.role] = cls

    def get(self, temperature: float = 0.0) -> BaseChatModel:
        """Return cached ChatModel for this role + temperature."""
        cache = type(self)._cache
        if temperature not in cache:
            cache[temperature] = self._make(temperature)
        return cache[temperature]

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    @abstractmethod
    def _make(self, temperature: float) -> BaseChatModel:
        """Build a new ChatModel instance."""


def _get_role_cls(role: str) -> type[BaseLLM]:
    cls = _ROLE_REGISTRY.get(role)
    if cls is None:
        raise ValueError(f"Unknown LLM role: {role!r}, registered: {list(_ROLE_REGISTRY)}")
    return cls


def get_llm(role: str = "default", temperature: float = 0.0) -> BaseChatModel:
    """Factory: return a cached ChatModel for the given role and temperature."""
    return _get_role_cls(role)().get(temperature)


def clear_llm_cache(role: str | None = None) -> None:
    """Clear caches.  role=None clears all roles."""
    if role is None:
        for cls in _ROLE_REGISTRY.values():
            cls.clear_cache()
    else:
        _get_role_cls(role).clear_cache()
