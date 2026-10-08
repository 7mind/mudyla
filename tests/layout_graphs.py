"""Deterministic dependency graphs for layout invariants and visual review."""

from dataclasses import dataclass

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionGraph, ActionKey, ActionNode, Dependency


@dataclass(frozen=True)
class GraphFixture:
    name: str
    graph: ActionGraph
    order: tuple[ActionKey, ...]
    width: int


def fixture(name: str, keys: list[ActionKey], dependencies: list[tuple[int, Dependency]],
            goals: list[int], width: int) -> GraphFixture:
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation(name, 1, key.id.name)))
             for key in keys}
    for target, dependency in dependencies:
        nodes[keys[target]].dependencies.add(dependency)
    return GraphFixture(name, ActionGraph(nodes, {keys[index] for index in goals}), tuple(keys), width)


def nested_forks() -> GraphFixture:
    keys = [ActionKey.from_name("shared-root")]
    pairs: list[tuple[int, int]] = []

    def node(role: str) -> int:
        index = len(keys)
        keys.append(ActionKey.from_name(f"{role}-{index}"))
        return index

    def branch(start: int, depth: int) -> int:
        if not depth:
            return start
        left, right = node("left"), node("right")
        pairs.extend(((start, left), (start, right)))
        left_end, right_end = branch(left, depth - 1), branch(right, depth - 1)
        end = node("merge")
        pairs.extend(((left_end, end), (right_end, end)))
        if start:
            pairs.append((0, end))
        return end

    goal = branch(0, 4)
    return fixture("nested-forks", keys, [(target, Dependency(keys[source])) for source, target in pairs], [goal], 140)


def layered_kinds() -> GraphFixture:
    contexts = [ContextId.from_dict({"platform": platform}) for platform in ("linux", "windows")]
    keys = [ActionKey.from_name(f"stage{layer}-{column % 3}", contexts[column // 3])
            for layer in range(5) for column in range(6)]
    dependencies = [(layer * 6 + column, Dependency(keys[(layer - 1) * 6 + parent]))
                    for layer in range(1, 5) for column in range(6)
                    for parent in (column, (column + 1) % 6, (column + 3) % 6)]
    dependencies.extend([(29, Dependency(keys[0])), (29, Dependency(keys[0], weak=True)),
        *( (29, Dependency(keys[0], soft=True, retainer_action=ActionKey.from_name("retain", context)))
           for context in contexts)])
    return fixture("layered-kinds", keys, dependencies, list(range(24, 30)), 160)


def repeated_diamonds() -> GraphFixture:
    keys = [ActionKey.from_name("seed")]
    pairs: list[tuple[int, int]] = []
    for group in range(18):
        previous = 4 * group
        left, right, work, merge = [previous + offset for offset in (1, 2, 3, 4)]
        keys.extend(ActionKey.from_name(f"block{group}-{role}") for role in ("left", "right", "work", "merge"))
        pairs.extend(((previous, left), (previous, right), (left, work), (right, work),
                      (left, merge), (right, merge), (work, merge)))
        for distance in (2, 5):
            if group >= distance:
                pairs.append((4 * (group - distance + 1), merge))
    return fixture("repeated-diamonds", keys, [(target, Dependency(keys[source])) for source, target in pairs], [72], 120)


def complex_fixtures() -> tuple[GraphFixture, ...]:
    return nested_forks(), layered_kinds(), repeated_diamonds()
