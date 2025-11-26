"""Tests for soft dependency functionality."""

import pytest
from pathlib import Path

from mudyla.ast.models import DependencyDeclaration, SourceLocation
from mudyla.dag import DAGBuilder, ActionKey, Dependency
from mudyla.dag.graph import ActionGraph
from mudyla.parser.dependency_parser import DependencyParser
from mudyla.parser.markdown_parser import MarkdownParser


def test_parse_soft_dependency_bash():
    """Test parsing soft dependency in bash syntax."""
    script = """
    dep action.strong-dep
    soft action.soft-target retain.action.my-retainer
    """
    location = SourceLocation("test.md", 1, "test-action")
    deps, _ = DependencyParser.find_all_dependencies(script, location)

    assert len(deps) == 2

    # First is strong
    assert deps[0].action_name == "strong-dep"
    assert deps[0].weak is False
    assert deps[0].soft is False
    assert deps[0].retainer_action is None

    # Second is soft
    assert deps[1].action_name == "soft-target"
    assert deps[1].weak is False
    assert deps[1].soft is True
    assert deps[1].retainer_action == "my-retainer"


def test_parse_soft_dependency_python():
    """Test parsing soft dependency in Python syntax."""
    script = """
    mdl.dep("action.strong-dep")
    mdl.soft("action.soft-target", "action.my-retainer")
    """
    location = SourceLocation("test.md", 1, "test-action")
    deps, _ = DependencyParser.find_all_dependencies(script, location)

    assert len(deps) == 2

    # First is strong
    assert deps[0].action_name == "strong-dep"
    assert deps[0].soft is False

    # Second is soft
    assert deps[1].action_name == "soft-target"
    assert deps[1].soft is True
    assert deps[1].retainer_action == "my-retainer"


def test_soft_dependency_str():
    """Test string representation of soft dependency declaration."""
    location = SourceLocation("test.md", 1, "test")

    soft_dep = DependencyDeclaration(
        action_name="target",
        location=location,
        soft=True,
        retainer_action="retainer"
    )
    assert str(soft_dep) == "soft target retain.retainer"

    strong_dep = DependencyDeclaration(
        action_name="target",
        location=location,
    )
    assert str(strong_dep) == "dep target"


def test_dependency_dataclass_soft():
    """Test Dependency dataclass with soft flag."""
    target_key = ActionKey.from_name("target")
    retainer_key = ActionKey.from_name("retainer")

    soft_dep = Dependency(
        action=target_key,
        soft=True,
        retainer_action=retainer_key
    )

    assert soft_dep.soft is True
    assert soft_dep.retainer_action == retainer_key
    assert "soft" in str(soft_dep)
    assert "retain" in str(soft_dep)


def test_get_pending_soft_dependencies(tmp_path: Path):
    """Test getting pending soft dependencies from graph."""
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: my-goal

```bash
soft action.soft-target retain.action.my-retainer
echo "Goal"
ret result:string=done
```

# action: soft-target

```bash
echo "Soft target"
ret value:string=soft
```

# action: my-retainer

```bash
echo "Retainer"
```

# action: hard-dep

```bash
echo "Hard dep"
ret value:string=hard
```

# action: another-goal

```bash
dep action.hard-dep
echo "Another goal"
ret result:string=another
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    builder = DAGBuilder(document)
    graph = builder.build_graph(["my-goal", "another-goal"], {})

    # Get pending soft dependencies
    pending = graph.get_pending_soft_dependencies()

    # Should have one pending soft dependency
    assert len(pending) == 1
    assert pending[0].soft is True
    assert pending[0].action == ActionKey.from_name("soft-target")
    assert pending[0].retainer_action == ActionKey.from_name("my-retainer")


def test_prune_with_retained_soft_targets(tmp_path: Path):
    """Test that prune_to_goals includes retained soft targets."""
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: my-goal

```bash
soft action.soft-target retain.action.my-retainer
echo "Goal"
ret result:string=done
```

# action: soft-target

```bash
echo "Soft target"
ret value:string=soft
```

# action: my-retainer

```bash
echo "Retainer"
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    builder = DAGBuilder(document)
    graph = builder.build_graph(["my-goal"], {})

    goal_key = ActionKey.from_name("my-goal")
    soft_target_key = ActionKey.from_name("soft-target")

    # Without retained soft targets
    pruned_without = graph.prune_to_goals()
    assert goal_key in pruned_without.nodes
    assert soft_target_key not in pruned_without.nodes  # Not retained

    # With retained soft targets
    retained = {soft_target_key}
    pruned_with = graph.prune_to_goals(retained)
    assert goal_key in pruned_with.nodes
    assert soft_target_key in pruned_with.nodes  # Now retained


def test_soft_dependency_already_strong(tmp_path: Path):
    """Test that soft dependency target reachable via strong path doesn't need retainer."""
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: my-goal

```bash
soft action.soft-target retain.action.my-retainer
dep action.strong-bridge
echo "Goal"
ret result:string=done
```

# action: soft-target

```bash
echo "Soft target"
ret value:string=soft
```

# action: strong-bridge

```bash
dep action.soft-target
echo "Bridge"
ret value:string=bridge
```

# action: my-retainer

```bash
echo "Retainer"
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    builder = DAGBuilder(document)
    graph = builder.build_graph(["my-goal"], {})

    # Get pending soft dependencies
    pending = graph.get_pending_soft_dependencies()

    # Should be empty - soft-target is already reachable via strong path
    assert len(pending) == 0


def test_soft_dependency_in_graph_node(tmp_path: Path):
    """Test that graph node has soft dependencies accessible."""
    md_file = tmp_path / "test.md"
    md_file.write_text("""\
# action: my-goal

```bash
soft action.soft-target retain.action.my-retainer
dep action.strong-dep
weak action.weak-dep
echo "Goal"
ret result:string=done
```

# action: soft-target

```bash
ret value:string=soft
```

# action: strong-dep

```bash
ret value:string=strong
```

# action: weak-dep

```bash
ret value:string=weak
```

# action: my-retainer

```bash
echo "Retainer"
```
""")

    parser = MarkdownParser()
    document = parser.parse_files([md_file])

    builder = DAGBuilder(document)
    graph = builder.build_graph(["my-goal"], {})

    goal_key = ActionKey.from_name("my-goal")
    goal_node = graph.nodes[goal_key]

    # Get different dependency types
    strong_deps = goal_node.get_strong_dependency_keys()
    weak_deps = goal_node.get_weak_dependency_keys()
    soft_deps = goal_node.get_soft_dependencies()

    assert ActionKey.from_name("strong-dep") in strong_deps
    assert ActionKey.from_name("weak-dep") in weak_deps
    assert len(soft_deps) == 1

    soft_dep = list(soft_deps)[0]
    assert soft_dep.action == ActionKey.from_name("soft-target")
    assert soft_dep.retainer_action == ActionKey.from_name("my-retainer")
