"""Tests for weak dependency functionality."""

import pytest
from pathlib import Path

from mudyla.ast.expansions import ActionExpansion, WeakActionExpansion
from mudyla.ast.models import DependencyDeclaration, SourceLocation
from mudyla.dag import DAGBuilder, ActionKey, Dependency
from mudyla.parser.dependency_parser import DependencyParser
from mudyla.parser.expansion_parser import ExpansionParser
from mudyla.parser.markdown_parser import MarkdownParser


def test_parse_weak_dependency_bash():
    """Test parsing weak dependency in bash syntax."""
    script = """
    dep action.strong-dep
    weak action.weak-dep
    """
    location = SourceLocation("test.md", 1, "test-action")
    deps, _ = DependencyParser.find_all_dependencies(script, location)

    assert len(deps) == 2

    # First is strong
    assert deps[0].action_name == "strong-dep"
    assert deps[0].weak is False

    # Second is weak
    assert deps[1].action_name == "weak-dep"
    assert deps[1].weak is True


def test_parse_weak_dependency_python():
    """Test parsing weak dependency in Python syntax."""
    script = """
    mdl.dep("action.strong-dep")
    mdl.weak("action.weak-dep")
    """
    location = SourceLocation("test.md", 1, "test-action")
    deps, _ = DependencyParser.find_all_dependencies(script, location)

    assert len(deps) == 2

    # First is strong
    assert deps[0].action_name == "strong-dep"
    assert deps[0].weak is False

    # Second is weak
    assert deps[1].action_name == "weak-dep"
    assert deps[1].weak is True


def test_parse_weak_action_expansion():
    """Test parsing ${action.weak.name.variable} expansion."""
    script = "echo ${action.strong.result} ${action.weak.optional.value}"
    expansions = ExpansionParser.find_all_expansions(script)

    assert len(expansions) == 2

    # First is strong
    assert isinstance(expansions[0], ActionExpansion)
    assert expansions[0].action_name == "strong"
    assert expansions[0].variable_name == "result"
    assert expansions[0].is_weak() is False

    # Second is weak
    assert isinstance(expansions[1], WeakActionExpansion)
    assert expansions[1].action_name == "optional"
    assert expansions[1].variable_name == "value"
    assert expansions[1].is_weak() is True


def test_weak_expansion_resolves_to_empty_when_missing():
    """Test that weak expansion returns empty string when action is missing."""
    expansion = WeakActionExpansion(
        original_text="${action.weak.missing.value}",
        action_name="missing",
        variable_name="value"
    )

    # Action not in context - should return empty string
    context = {"actions": {}}
    assert expansion.resolve(context) == ""

    # Variable not in action outputs - should return empty string
    context = {"actions": {"missing": {}}}
    assert expansion.resolve(context) == ""

    # Value is None - should return empty string
    context = {"actions": {"missing": {"value": None}}}
    assert expansion.resolve(context) == ""

    # Value exists - should return it
    context = {"actions": {"missing": {"value": "hello"}}}
    assert expansion.resolve(context) == "hello"


def test_weak_dependency_pruning(tmp_path: Path):
    """Test that weak dependencies are pruned when target has no strong path."""
    # Create a test markdown file with weak dependency
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: my-goal

```bash
weak action.optional-action
echo "Goal"
ret result:string=done
```

# action: optional-action

```bash
echo "Optional"
ret value:string=optional-value
```

# action: required-action

```bash
dep action.optional-action
echo "Required"
ret value:string=required-value
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    # Test 1: my-goal only depends weakly on optional-action
    # optional-action should be PRUNED (no strong path)
    builder = DAGBuilder(document)
    graph = builder.build_graph(["my-goal"], {})
    pruned = graph.prune_to_goals()

    goal_key = ActionKey.from_name("my-goal")
    optional_key = ActionKey.from_name("optional-action")

    assert goal_key in pruned.nodes
    assert optional_key not in pruned.nodes  # Pruned because only weak dependency

    # Test 2: If we add 'required-action' as a goal, which has strong dep on optional-action
    # then optional-action should be RETAINED
    graph2 = builder.build_graph(["my-goal", "required-action"], {})
    pruned2 = graph2.prune_to_goals()

    required_key = ActionKey.from_name("required-action")

    assert goal_key in pruned2.nodes
    assert required_key in pruned2.nodes
    assert optional_key in pruned2.nodes  # Retained because required-action has strong dep on it


def test_weak_dependency_in_graph_builder(tmp_path: Path):
    """Test that DAGBuilder creates Dependency objects with correct weak flag."""
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: test-action

```bash
dep action.strong-dep
weak action.weak-dep
echo "Test: ${action.strong-dep.value} ${action.weak.weak-dep.result}"
ret result:string=done
```

# action: strong-dep

```bash
ret value:string=strong
```

# action: weak-dep

```bash
ret result:string=weak
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    builder = DAGBuilder(document)
    graph = builder.build_graph(["test-action"], {})

    test_key = ActionKey.from_name("test-action")
    test_node = graph.nodes[test_key]

    # Should have 2 dependencies total:
    # - strong-dep (from both dep declaration and ${action.strong-dep.value})
    # - weak-dep (from both weak declaration and ${action.weak.weak-dep.result})
    # The sets de-duplicate by action key
    assert len(test_node.dependencies) == 2

    # Convert to list for easier checking
    deps = list(test_node.dependencies)

    # Check we have the right mix of strong and weak
    strong_deps = [d for d in deps if not d.weak]
    weak_deps = [d for d in deps if d.weak]

    assert len(strong_deps) == 1  # strong-dep (de-duplicated from declaration + expansion)
    assert len(weak_deps) == 1    # weak-dep (de-duplicated from weak declaration + weak expansion)

    # Verify the dependency keys are correct
    strong_dep_keys = {d.action for d in strong_deps}
    weak_dep_keys = {d.action for d in weak_deps}

    assert ActionKey.from_name("strong-dep") in strong_dep_keys
    assert ActionKey.from_name("weak-dep") in weak_dep_keys


def test_dependency_dataclass():
    """Test Dependency dataclass properties."""
    strong_dep = Dependency(action=ActionKey.from_name("test"), weak=False)
    weak_dep = Dependency(action=ActionKey.from_name("test"), weak=True)

    assert str(strong_dep) == "test"
    assert str(weak_dep) == "weak test"

    # Test equality
    assert strong_dep != weak_dep  # Different weak flag
    assert strong_dep == Dependency(action=ActionKey.from_name("test"), weak=False)


def test_weak_dependency_validation_skip(tmp_path: Path):
    """Test that validator skips missing weak dependencies."""
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: my-goal

```bash
weak action.nonexistent
echo "Goal with weak dep on non-existent action"
ret result:string=done
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    builder = DAGBuilder(document)
    graph = builder.build_graph(["my-goal"], {})

    # This should NOT raise an error even though 'nonexistent' doesn't exist
    # because it's a weak dependency and will be pruned
    pruned = graph.prune_to_goals()

    goal_key = ActionKey.from_name("my-goal")
    assert goal_key in pruned.nodes
    assert len(pruned.nodes) == 1  # Only my-goal, weak dep was pruned


def test_mixed_strong_and_weak_dependencies(tmp_path: Path):
    """Test action with both strong and weak dependencies."""
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: my-goal

```bash
dep action.required
weak action.optional
echo "Required: ${action.required.value}"
echo "Optional: ${action.weak.optional.value}"
ret result:string=done
```

# action: required

```bash
ret value:string=required-value
```

# action: optional

```bash
ret value:string=optional-value
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    builder = DAGBuilder(document)
    graph = builder.build_graph(["my-goal"], {})
    pruned = graph.prune_to_goals()

    goal_key = ActionKey.from_name("my-goal")
    required_key = ActionKey.from_name("required")
    optional_key = ActionKey.from_name("optional")

    # my-goal and required should be in graph (strong dependency)
    assert goal_key in pruned.nodes
    assert required_key in pruned.nodes

    # optional should be pruned (only weak dependency)
    assert optional_key not in pruned.nodes

    # Verify the dependencies in the pruned graph
    goal_node = pruned.nodes[goal_key]
    dep_keys = {d.action for d in goal_node.dependencies}

    # Only required should remain (optional was pruned)
    assert required_key in dep_keys
    assert optional_key not in dep_keys
