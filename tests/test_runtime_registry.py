from mudyla.executor.runtime_registry import RuntimeRegistry
from mudyla.executor.runtime_bash import BashRuntime
from mudyla.executor.runtime_python import PythonRuntime


def test_runtime_registry_registers_and_returns_default_languages():
    RuntimeRegistry.register(BashRuntime)
    RuntimeRegistry.register(PythonRuntime)

    bash = RuntimeRegistry.get("bash")
    python = RuntimeRegistry.get("python")

    assert isinstance(bash, BashRuntime)
    assert isinstance(python, PythonRuntime)

    languages = {runtime.get_language_name() for runtime in RuntimeRegistry.all()}
    assert "bash" in languages
    assert "python" in languages
