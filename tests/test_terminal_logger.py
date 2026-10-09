"""The same run logger receives planning, retention and action reports."""

from pathlib import Path
import re
from rich.cells import cell_len
from rich.text import Text

import pytest

from mudyla.cli import CLI
from mudyla.executor.engine import ExecutionEngine
from mudyla.executor.retainer_executor import RetainerExecutor
from mudyla.logging.retainer_display import PlanningFacts
from mudyla.logging.terminal_logger import TerminalLogger, create_terminal_logger


def _single_action_project(root: Path) -> None:
    (root / ".git").mkdir()
    definitions = root / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('''# action: goal

```python
print("goal output")
```
''', encoding="utf-8")


@pytest.mark.parametrize("dry_run", [False, True])
def test_final_planning_facts_appear_in_plan_or_actions_only(tmp_path, monkeypatch, capsys, dry_run):
    _single_action_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    options = ["--without-nix", "--no-color", "--plan", "table", ":goal"]
    if dry_run:
        options.insert(0, "--dry-run")
    assert CLI().run(options) == 0
    text = capsys.readouterr().out
    assert "Built plan graph" not in text
    assert re.search(r"1 action with 0 retained, planned in \d+ms", text)


@pytest.mark.parametrize("count, expected", [
    (None, "0 retained, planning took 13ms"),
    (1, "1 action with 0 retained, planned in 13ms"),
    (4, "4 actions with 0 retained, planned in 13ms"),
])
def test_planning_summary_uses_known_facts_and_natural_copy(count, expected):
    summary = PlanningFacts(12.8, frozenset(), count).summary()
    assert summary.plain == expected
    assert summary.style == "dim"


def test_run_identity_precedes_planning_and_matches_execution_directory(tmp_path, monkeypatch, capsys):
    _single_action_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    observed: list[Path] = []

    class ObservedEngine(ExecutionEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            observed.append(self.run_directory)

    monkeypatch.setattr("mudyla.cli.ExecutionEngine", ObservedEngine)
    assert CLI().run(["--without-nix", "--no-color", "--plan", "table", "--keep-run-dir", ":goal"]) == 0
    text = capsys.readouterr().out
    assert text.index("Run ID:") < text.index("Contexts:")
    assert text.count("Run ID:") == 1
    identity = re.search(r"Run ID:\s*(\S+)", text).group(1)
    assert observed == [tmp_path / ".mdl" / "runs" / identity]
    assert observed[0].is_dir()


@pytest.mark.parametrize("mode", ["pure", "table", "simple", "verbose", "github", "teamcity"])
@pytest.mark.parametrize("fails", [False, True])
def test_incremental_run_info_fields_align_before_contexts_or_preparation_error(tmp_path, monkeypatch, capsys, mode, fails):
    from mudyla.dag.compiler import DAGCompiler

    _single_action_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    early = []

    class ObservedCompiler(DAGCompiler):
        def compile(self):
            early.append(capsys.readouterr().out)
            assert "Run ID:" in early[-1] and "Project root:" in early[-1] and "Definitions:" in early[-1]
            if fails:
                raise ValueError("COMPILATION_FAILURE")
            return super().compile()

    monkeypatch.setattr("mudyla.cli.DAGCompiler", ObservedCompiler)
    options = ["--without-nix", "--no-color", "--dry-run", "--plan", "table", "--logger", mode, ":goal"]
    if mode == "table":
        options.insert(0, "--force-interactive")
    assert CLI().run(options) == int(fails)
    capture = capsys.readouterr()
    assert capture.err == ""
    text = Text.from_ansi("".join(early) + capture.out).plain
    if mode == "teamcity":
        from mudyla.logging.teamcity import parse_message
        messages = [parse_message(line) for line in text.splitlines()]
        assert all(message is not None for message in messages)
        text = "".join(message.attributes.get("text", "") for message in messages)
    assert text.count("Run info:") == text.count("Run ID:") == 1
    columns = []
    for name in ("Run ID", "Using Nix", "Project root", "Execution mode", "Definitions"):
        matching = [(line, re.search(rf"{name}:\s+(\S)", line)) for line in text.splitlines()]
        line, match = next((line, match) for line, match in matching if match is not None)
        columns.append(cell_len(line[:match.start(1)]))
    assert len(set(columns)) == 1
    end = text.index("COMPILATION_FAILURE" if fails else "Contexts:")
    assert text.index("Definitions:") < end
    assert not (tmp_path / ".mdl" / "runs").exists()


@pytest.mark.parametrize("options", [["--help"], ["--without-nix", "--no-color"],
    ["--without-nix", "--no-color", "--dry-run", ":goal"]])
def test_nonexecuting_paths_do_not_create_run_directories(tmp_path, monkeypatch, options):
    _single_action_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert CLI().run(options) == (1 if options == ["--without-nix", "--no-color"] else 0)
    assert not (tmp_path / ".mdl" / "runs").exists()


@pytest.mark.parametrize("mode", ["pure", "simple", "verbose", "github", "teamcity"])
def test_append_only_retainer_phase_has_one_heading_and_each_completed_block(tmp_path, monkeypatch, capsys, mode):
    _single_action_project(tmp_path)
    (tmp_path / ".mdl" / "defs" / "actions.md").write_text('''# action: goal
```python
mdl.soft("action.alpha", "action.keep-alpha")
mdl.soft("action.beta", "action.keep-beta")
```
# action: alpha
```python
pass
```
# action: beta
```python
pass
```
# action: keep-alpha
```python
print("ALPHA_COMPLETED", flush=True)
mdl.retain()
```
# action: keep-beta
```python
print("BETA_COMPLETED", flush=True)
mdl.retain()
```
''', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert CLI().run(["--without-nix", "--no-color", "--logger", mode, ":goal"]) == 0
    text = capsys.readouterr().out
    if mode == "teamcity":
        from mudyla.logging.teamcity import parse_message
        messages = [parse_message(line) for line in text.splitlines()]
        assert all(message is not None for message in messages)
        text = "".join(message.attributes.get("text", "") for message in messages if message is not None)
    assert text.count("Retainers:") == 1
    for marker in ("ALPHA_COMPLETED", "BETA_COMPLETED"):
        assert marker in text[:text.index("Goals:")]
    assert text.count("retained alpha @global") == text.count("retained beta @global") == 1


@pytest.mark.parametrize("mode", ["pure", "table", "simple", "verbose", "github", "teamcity"])
def test_cli_passes_same_upfront_logger_to_retainer_and_action_producers(tmp_path, monkeypatch, mode):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('''# action: goal

```python
mdl.soft("action.target", "action.keep")
```

# action: target

```python
print("target output")
```

# action: keep

```python
print("retainer output", flush=True)
mdl.retain()
```
''', encoding="utf-8")
    receivers: dict[str, object] = {}
    loggers: list[TerminalLogger] = []

    def create(*args, **kwargs):
        logger = create_terminal_logger(*args, **kwargs)
        assert logger.graph is None and logger.execution_order is None
        assert not logger._actions_initialized
        assert not hasattr(logger, "tasks") and not hasattr(logger, "action_keys")
        assert not hasattr(logger, "renderer")
        assert logger.retainers.output is logger.output
        assert logger.retainers.session is logger.session
        loggers.append(logger)
        return logger

    class ObservedRetainer(RetainerExecutor):
        def __init__(self, *args, **kwargs):
            receivers["retainer"] = kwargs["observer"]
            assert kwargs["observer"] is loggers[0]
            assert not loggers[0]._actions_initialized
            super().__init__(*args, **kwargs)

    class ObservedEngine(ExecutionEngine):
        def __init__(self, *args, **kwargs):
            receivers["actions"] = kwargs.get("logger")
            assert kwargs["logger"] is loggers[0]
            assert loggers[0].retainers.finished and not loggers[0]._actions_initialized
            assert loggers[0].retainers.refresh_thread is None or not loggers[0].retainers.refresh_thread.is_alive()
            super().__init__(*args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mudyla.cli.RetainerExecutor", ObservedRetainer)
    monkeypatch.setattr("mudyla.cli.ExecutionEngine", ObservedEngine)
    monkeypatch.setattr("mudyla.cli.create_terminal_logger", create)
    assert CLI().run(["--without-nix", "--no-color", "--logger", mode, "--force-interactive", "--plan", "table", ":goal"]) == 0
    assert len(loggers) == 1
    assert loggers[0].mode.value == mode and loggers[0]._actions_initialized and loggers[0].finished
    assert receivers["retainer"] is receivers["actions"], "CLI supplied different planning and action loggers"
