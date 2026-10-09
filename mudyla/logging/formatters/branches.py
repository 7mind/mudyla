"""Stable colors for maximal nonbranching dependency paths."""

from enum import Enum

from ...dag.graph import ActionKey
from ...dag.solver.model import DagEdge


class BranchTheme(Enum):
    DISABLED = 'disabled'
    TERMINAL = 'terminal'
    DARK = 'dark'
    LIGHT = 'light'


_TERMINAL_COLORS = ('cyan', 'magenta', 'blue', 'green', 'yellow', 'red')
_DARK_COLORS = ('#56b4e9', '#cc79a7', '#8b9cf4', '#69c58e', '#e6b85c', '#ef887a')
_LIGHT_COLORS = ('#007fa8', '#a44483', '#555db0', '#287a47', '#956600', '#b24738')

CanonicalKey = tuple[str, tuple[tuple[str, str], ...], tuple[tuple[str, int, tuple[str, ...]], ...], tuple[tuple[str, bool], ...]]


def _key_order(key: ActionKey) -> CanonicalKey:
    context = key.context_id
    arguments = tuple((name, 0, (value,)) if isinstance(value, str) else (name, 1, value)
                      for name, value in context.args)
    return key.id.name, context.axis_values, arguments, context.flags


def branch_segments(edges: tuple[DagEdge, ...]) -> tuple[int, ...]:
    pairs = {(edge.source, edge.target) for edge in edges}
    incoming: dict[ActionKey, set[ActionKey]] = {}
    outgoing: dict[ActionKey, set[ActionKey]] = {}
    for source, target in pairs:
        incoming.setdefault(target, set()).add(source)
        outgoing.setdefault(source, set()).add(target)
    starts = sorted((pair for pair in pairs if len(incoming.get(pair[0], ())) != 1
                     or len(outgoing[pair[0]]) != 1),
                    key=lambda pair: (_key_order(pair[0]), _key_order(pair[1])))
    assignments: dict[tuple[ActionKey, ActionKey], int] = {}
    for segment, (source, target) in enumerate(starts):
        for _ in range(len(pairs)):
            assert (source, target) not in assignments
            assignments[source, target] = segment
            if len(incoming[target]) != 1 or len(outgoing.get(target, ())) != 1:
                break
            source, target = target, next(iter(outgoing[target]))
        else:
            raise AssertionError('Dependency path did not terminate')
    assert len(assignments) == len(pairs), 'Every dependency must belong to a path segment'
    return tuple(assignments[edge.source, edge.target] for edge in edges)


def branch_palette(theme: BranchTheme) -> tuple[str, ...]:
    return {BranchTheme.DISABLED: (), BranchTheme.TERMINAL: _TERMINAL_COLORS,
            BranchTheme.DARK: _DARK_COLORS, BranchTheme.LIGHT: _LIGHT_COLORS}[theme]
