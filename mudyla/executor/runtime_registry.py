"""Runtime registry for language runtimes."""

from collections.abc import Iterable
from typing import Dict, Type

from mudyla.executor.language_runtime import LanguageRuntime


class RuntimeRegistry:
    """Registry for available language runtimes."""

    _registry: Dict[str, Type[LanguageRuntime]] = {}

    @classmethod
    def register(cls, runtime_cls: Type[LanguageRuntime]) -> None:
        """Register a LanguageRuntime implementation."""
        runtime = runtime_cls()
        language_name = runtime.get_language_name()
        cls._registry[language_name] = runtime_cls

    @classmethod
    def get(cls, language: str) -> LanguageRuntime:
        """Get a runtime by language name."""
        if language not in cls._registry:
            raise ValueError(
                f"Unsupported language: {language}. "
                f"Supported languages: {', '.join(sorted(cls._registry.keys()))}"
            )
        return cls._registry[language]()

    @classmethod
    def all(cls) -> Iterable[LanguageRuntime]:
        """Iterate over all registered runtimes."""
        for runtime_cls in cls._registry.values():
            yield runtime_cls()
