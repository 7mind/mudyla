"""Runtime registry for language runtimes."""

from collections.abc import Iterable

from mudyla.executor.language_runtime import LanguageRuntime


class RuntimeRegistry:
    """Registry for available language runtimes."""

    _registry: dict[str, type[LanguageRuntime]] = {}

    @classmethod
    def register(cls, runtime_cls: type[LanguageRuntime]) -> None:
        """Register a LanguageRuntime implementation."""
        runtime = runtime_cls()
        language_name = runtime.get_language_name()
        cls._registry[language_name] = runtime_cls

    @classmethod
    def ensure_registered(cls, runtime_cls: type[LanguageRuntime]) -> None:
        """Register runtime only if not present."""
        runtime = runtime_cls()
        language_name = runtime.get_language_name()
        if language_name not in cls._registry:
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
